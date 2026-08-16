# Alerting fan-out: Alertmanager -> SNS -> email.
#
# Alertmanager has a native `sns_config` receiver, so no bridge service is needed —
# it signs a Publish call with SigV4 and SNS does the delivery.
#
# ── Why a SEPARATE topic from the reminders one ─────────────────────────────
# sns.tf already declares a `reminders` topic, and reusing it would be one fewer
# resource. It is deliberately not reused: that topic is a PRODUCT feature — the
# daily "cards due" digest a learner subscribes to — and this one is operational
# alerting for whoever runs the cluster. Merging them would mail every learner
# about a p95 latency breach, and mail the operator about study reminders. They
# also want different subscribers and different retention of attention.
#
# ── How Alertmanager authenticates ─────────────────────────────────────────
# It holds no access key. The Alertmanager pod runs on a worker node, and the AWS
# SDK inside it falls back to the instance metadata service, picking up the WORKER
# instance role's temporary credentials. That role is granted `sns:Publish` on this
# topic only — see the alerts_topic_arn input threaded into modules/k8s-cluster.
# The launch template already sets http_put_response_hop_limit = 2, which is what
# lets a container — one network hop further out than the host — reach IMDSv2 at
# all. That setting was added for the app's own credentials and happens to be
# exactly what Alertmanager needs too.
#
# ── The email subscription needs ONE manual click ──────────────────────────
# AWS sends a confirmation link to the address the first time this is applied, and
# SNS delivers nothing until it is clicked. Terraform reports the subscription as
# created either way, so `PendingConfirmation` is the thing to check when alerts
# fire but no mail arrives. The confirmation survives `terraform destroy` as long as
# the topic ARN is unchanged — and it is, because the name derives from the
# deterministic name_prefix rather than from anything random.

resource "aws_sns_topic" "alerts" {
  name = "${local.name_prefix}-alerts"
  # Becomes the email's From name. SNS caps display_name at 10 characters when the
  # topic is used for SMS, so it is kept short.
  display_name = "Recall"

  tags = { Name = "${local.name_prefix}-alerts" }
}

resource "aws_sns_topic_subscription" "alerts_email" {
  # count rather than an unconditional resource: with no address configured this
  # stack still applies cleanly and Alertmanager still publishes — the alerts just
  # go nowhere until someone subscribes. Failing the apply over a missing email
  # would block the whole cluster on an optional convenience.
  count = var.alert_email == "" ? 0 : 1

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email

  # `email` subscriptions cannot be reconciled the way HTTP ones can: AWS returns
  # the ARN as the literal string "pending confirmation" until the link is clicked,
  # which Terraform would otherwise try to fix on every plan.
  lifecycle {
    ignore_changes = [confirmation_timeout_in_minutes]
  }
}
