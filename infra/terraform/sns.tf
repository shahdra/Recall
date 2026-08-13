# SNS topic for the daily "you have cards due" reminder (docs/spec.md:226, Flow C).
#
# The reminder CronJob (infra/k8s/<env>/reminder/) counts due cards each morning
# and publishes ONE digest here.
#
# Why one topic and one digest rather than per-learner notifications: Recall has no
# authentication. A learner is a random id generated in the browser and kept in
# localStorage (services/frontend/components/app-shell.tsx:21-35), so
# "learner-a3f9x2k1" has no email address and no phone number — there is nothing to
# address a personal reminder to. The digest is therefore aggregate
# ("3 cards due today across 2 learners") and goes to whoever subscribed to the
# topic, which in practice is the developer watching the demo.

resource "aws_sns_topic" "reminders" {
  name = "${local.name_prefix}-reminders"

  # Encrypt at rest with the AWS-managed SNS key. Free, and the alternative is a
  # CMK plus kms:GenerateDataKey on the publisher's policy for a message whose
  # entire content is a card count.
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Name = "${local.name_prefix}-reminders"
  }
}

# Deliberately NO aws_sns_topic_subscription resource.
#
# An email subscription is only live once the recipient clicks a confirmation link
# in their inbox. Terraform can create the subscription but cannot click the link,
# so the resource would sit in PendingConfirmation forever while state claims it is
# created — and every subsequent plan would show it as unchanged, which is worse
# than not having it. RUNBOOK.md documents the one-time `aws sns subscribe` call.
#
# An SQS or Lambda subscription needs no confirmation and would be a legitimate
# resource here; neither exists in this project.

# Restrict publishing to this account. Without a policy, a topic's default policy
# is already account-scoped, but being explicit means a future cross-account grant
# has to be an intentional edit to this block rather than an oversight.
resource "aws_sns_topic_policy" "reminders" {
  arn    = aws_sns_topic.reminders.arn
  policy = data.aws_iam_policy_document.reminders_topic.json
}

data "aws_iam_policy_document" "reminders_topic" {
  statement {
    sid    = "AllowOwningAccountPublish"
    effect = "Allow"

    principals {
      type        = "AWS"
      identifiers = [data.aws_caller_identity.current.account_id]
    }

    actions   = ["SNS:Publish", "SNS:Subscribe", "SNS:GetTopicAttributes"]
    resources = [aws_sns_topic.reminders.arn]
  }
}
