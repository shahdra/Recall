# Credentials for the pods.
#
# WHY AN IAM USER AND NOT A ROLE: the cluster Recall deploys onto is kubeadm on
# EC2, with no OIDC provider, so there is no IRSA and a pod cannot assume a role by
# service account. The worker node's instance role grants only SSM and ECR — no
# DynamoDB, S3, SNS or Bedrock — so inheriting the node role gives the pods nothing.
# That leaves static access keys delivered through a Kubernetes Secret, which is
# what the reference project on the same cluster does.
#
# The keys are NOT created here. `aws_iam_access_key` writes the secret access key
# into Terraform state in plaintext, and this state lives in an S3 bucket in an
# account shared with the course. RUNBOOK.md documents creating the key pair with
# the CLI and loading it straight into the Secret.
#
# EVERY STATEMENT IS SCOPED TO A NAMED ARN. Never "Resource": "*" — the account is
# shared, so a wildcard would hand Recall's pods read/write access to a
# classmate's tables.

resource "aws_iam_user" "app" {
  name = "${local.name_prefix}-app"
  path = "/recall/"

  tags = {
    Name = "${local.name_prefix}-app"
  }
}

resource "aws_iam_user_policy" "app" {
  name   = "${local.name_prefix}-app"
  user   = aws_iam_user.app.name
  policy = data.aws_iam_policy_document.app.json
}

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
