# The application's AWS permissions.
#
# This file defines WHAT the pods may do. WHERE it is attached is
# modules/k8s-cluster/main.tf, which binds this document inline to the worker node
# role; pods inherit it through the instance metadata service, and boto3's default
# credential chain finds it with no configuration. Nothing in services/ reads
# AWS_ACCESS_KEY_ID, so this needed no application change.
#
# WHY A NODE ROLE AND NOT AN IAM USER WITH STATIC KEYS.
# The earlier design here was a dedicated `-app` IAM user whose access keys were
# minted with the CLI and loaded into the `recall-secrets` Kubernetes Secret. That is
# what the reference project on the same cluster does, and it has one genuine
# advantage: permissions scope per-workload rather than per-node.
#
# It was replaced because it created a credential treadmill. `terraform destroy`
# deletes the user, so its keys die with every teardown — and this project's whole
# cost strategy is destroy-and-reapply, because the course budget keeper stops EC2
# twice daily and a stopped kubeadm control plane cannot be restarted. Every cycle
# would mean minting a new key pair and re-pasting it into a GitHub secret and a local
# env file. Credentials that must be re-copied on every apply get copied wrong, and a
# stale key fails at runtime looking like an application bug.
#
# The node role has none of that: nothing to mint, nothing to store, nothing to
# rotate, and AWS hands out short-lived credentials instead of permanent ones.
#
# WHAT IT GIVES UP: every pod on a worker gets these permissions, not only Recall's
# four. Pod-level scoping is IRSA's job, and IRSA requires an OIDC provider that a
# kubeadm cluster does not have. On a single-tenant lab cluster destroyed nightly the
# distinction costs nothing; on a shared production cluster it would not be
# acceptable.
#
# EVERY STATEMENT IS SCOPED TO A NAMED ARN. Never "Resource": "*" — the account is
# shared, so a wildcard would hand Recall's pods read/write access to a classmate's
# tables.

data "aws_iam_policy_document" "app" {
  # --- DynamoDB -------------------------------------------------------------
  #
  # No DeleteTable, CreateTable or UpdateTable: schema changes belong to
  # Terraform, and a pod that can drop the Cards table is one bug away from
  # deleting every learner's history.
  statement {
    sid    = "CardsDecksProfileCrud"
    effect = "Allow"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
      "dynamodb:DescribeTable",
    ]

    resources = [
      aws_dynamodb_table.cards.arn,
      aws_dynamodb_table.decks.arn,
      aws_dynamodb_table.learner_profile.arn,

      # A GSI is a separate resource for authorization: a Query against
      # due-index is denied by a policy that names only the table ARN. This
      # single line is the difference between the study session working and
      # every request failing with AccessDeniedException.
      "${aws_dynamodb_table.cards.arn}/index/*",
    ]
  }

  # --- S3 -------------------------------------------------------------------
  #
  # Object actions are scoped to the uploads/ prefix rather than the whole bucket,
  # so a key-construction bug cannot scatter objects across the bucket root.
  #
  # GetObject is included even though nothing reads uploads back today
  # (tutor-agent only calls put_object). Re-running ingestion against an archived
  # file is the reason the bucket exists, and that is a read.
  statement {
    sid    = "UploadsObjectAccess"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:GetObject",
    ]

    resources = ["${aws_s3_bucket.uploads.arn}/uploads/*"]
  }

  # ListBucket is a BUCKET-level action, so its resource is the bucket ARN with no
  # key suffix — a common mistake is to list it alongside the object actions above,
  # where it silently never matches. Constrained by prefix so it cannot enumerate
  # anything outside uploads/.
  statement {
    sid    = "UploadsList"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
    ]
    resources = [aws_s3_bucket.uploads.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["uploads/*"]
    }
  }

  # Fluent Bit ships container logs from every node. PutObject only, scoped to the
  # logs/ prefix: the shipper never reads, lists, or deletes, so a compromised
  # sidecar cannot exfiltrate the archive it writes into. That asymmetry is the
  # reason GetObject is absent here while it is present for uploads/ above.
  statement {
    sid    = "ShipContainerLogs"
    effect = "Allow"

    actions = [
      "s3:PutObject",
    ]

    resources = ["${aws_s3_bucket.logs.arn}/logs/*"]
  }

  # Multipart needs these, and only on the parts being uploaded. Without them a
  # log object big enough to trigger multipart fails halfway with an AccessDenied
  # that reads like a bucket-policy problem and is not one.
  statement {
    sid    = "CompleteMultipartLogUploads"
    effect = "Allow"

    actions = [
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = ["${aws_s3_bucket.logs.arn}/logs/*"]
  }

  # --- SNS ------------------------------------------------------------------
  #
  # Publish only. The reminder CronJob sends the digest; nothing in Recall
  # subscribes, unsubscribes, or reads the topic's subscriber list.
  statement {
    sid       = "PublishReminders"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.reminders.arn]
  }

  # The topic is encrypted with alias/aws/sns, so publishing requires a data key
  # from KMS. Without this the publish fails with KMSAccessDenied, which reads like
  # an SNS permission problem and is not one.
  statement {
    sid    = "SnsKmsDataKey"
    effect = "Allow"

    actions = [
      "kms:GenerateDataKey",
      "kms:Decrypt",
    ]

    resources = ["arn:aws:kms:${var.region}:${data.aws_caller_identity.current.account_id}:alias/aws/sns"]
  }

  # --- Bedrock --------------------------------------------------------------
  #
  # InvokeModel only. No InvokeModelWithResponseStream: the agent's ReAct loop
  # needs whole responses to parse tool calls, and it never streams.
  #
  # NOTE: this policy grants; it does not override. The account-wide
  # `bedrock-restrict-developers` policy carries an EXPLICIT DENY for any model
  # outside its eight-model allowlist, and an explicit deny always wins. Both
  # models below are on that allowlist and were verified to support tool calling —
  # a model that cannot call tools cannot drive the loop (see llm.py:7-9).
  statement {
    sid       = "InvokeChatModels"
    effect    = "Allow"
    actions   = ["bedrock:InvokeModel"]
    resources = local.bedrock_model_arns
  }
}

locals {
  # The primary model id carries a "us." prefix
  # (us.anthropic.claude-haiku-4-5-20251001-v1:0), which makes it a CROSS-REGION
  # INFERENCE PROFILE, not a plain foundation model. Invoking one requires two
  # kinds of ARN:
  #
  #   1. the inference-profile ARN in the calling region, and
  #   2. the foundation-model ARN in EVERY region the profile may route to.
  #
  # Granting only (1) fails intermittently — it works until the profile happens to
  # route a request to us-west-2, then returns AccessDeniedException on a request
  # identical to one that just succeeded. That is a genuinely awful thing to debug,
  # so all three US regions are listed.
  bedrock_us_regions = ["us-east-1", "us-east-2", "us-west-2"]

  bedrock_foundation_models = [
    "anthropic.claude-haiku-4-5-20251001-v1:0", # llm.py DEFAULT_MODEL (via us. profile)
    "amazon.nova-lite-v1:0",                    # llm.py DEFAULT_FALLBACK_MODEL
  ]

  bedrock_model_arns = concat(
    # Foundation models, per region the inference profile can route to.
    flatten([
      for region in local.bedrock_us_regions : [
        for model in local.bedrock_foundation_models :
        "arn:aws:bedrock:${region}::foundation-model/${model}"
      ]
    ]),

    # The inference profile itself, which is account-scoped and lives in the
    # calling region.
    [
      "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.current.account_id}:inference-profile/us.anthropic.claude-haiku-4-5-20251001-v1:0",
    ],
  )
}
