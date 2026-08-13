# These outputs ARE the Kubernetes ConfigMap.
#
# Every value below appears as an env var in infra/k8s/<env>/configmap.yaml, under
# the name given in its description. After an apply, `terraform output` is the
# source those manifests are updated from — the names are not guessable, since the
# bucket carries an account id and the topic ARN carries both account and region.
#
#   terraform output -json | jq -r '.configmap_env.value | to_entries[] | "\(.key)=\(.value)"'
#
# Nothing secret is output. The app user's access key is created out-of-band with
# the CLI precisely so it never lands in state (see iam.tf), and outputs read from
# state.

output "cards_table_name" {
  description = "RECALL_CARDS_TABLE"
  value       = aws_dynamodb_table.cards.name
}

output "decks_table_name" {
  description = "RECALL_DECKS_TABLE"
  value       = aws_dynamodb_table.decks.name
}

output "profile_table_name" {
  description = "RECALL_PROFILE_TABLE"
  value       = aws_dynamodb_table.learner_profile.name
}

output "uploads_bucket" {
  description = "RECALL_S3_BUCKET"
  value       = aws_s3_bucket.uploads.bucket
}

output "reminders_topic_arn" {
  description = "RECALL_SNS_TOPIC_ARN — the reminder CronJob publishes the daily digest here."
  value       = aws_sns_topic.reminders.arn
}

output "app_iam_user" {
  description = <<-EOT
    IAM user whose access keys the pods use. Create the key pair with:
      aws iam create-access-key --user-name <this value>
    Keys are deliberately not managed by Terraform — see iam.tf.
  EOT
  value       = aws_iam_user.app.name
}

# Convenience shape for copying into the ConfigMap in one step, rather than
# reading six outputs and hand-matching each to its env var name.
output "configmap_env" {
  description = "Every non-secret env var the services need, keyed by env var name."
  value = {
    RECALL_CARDS_TABLE   = aws_dynamodb_table.cards.name
    RECALL_DECKS_TABLE   = aws_dynamodb_table.decks.name
    RECALL_PROFILE_TABLE = aws_dynamodb_table.learner_profile.name
    RECALL_S3_BUCKET     = aws_s3_bucket.uploads.bucket
    RECALL_SNS_TOPIC_ARN = aws_sns_topic.reminders.arn
    AWS_REGION           = var.region
  }
}

output "cards_due_index_arn" {
  description = <<-EOT
    ARN of the due-date GSI. Not consumed by any manifest — it is here because a
    Query against an index is authorized against the INDEX arn, not the table's,
    so this is the value to check first when a study session returns
    AccessDeniedException while everything else works.
  EOT
  value       = "${aws_dynamodb_table.cards.arn}/index/due-index"
}
