# Archive bucket for uploaded study material.
#
# The tutor-agent writes the raw PDF/text here under uploads/<user_id>/<uuid>
# (services/tutor-agent/app.py:490-502) after extracting its text. Nothing ever
# reads it back — the cards live in DynamoDB. It exists so an upload that produced
# bad cards can be re-examined, and so ingestion can be re-run against the original
# file if the extractor improves.
#
# The write is best-effort by design: app.py:499 catches every exception and drops
# the archive rather than failing the upload, so an outage here costs a learner
# nothing — which is part of why this bucket is safe to make destroyable.

resource "aws_s3_bucket" "uploads" {
  # Bucket names are globally unique across ALL AWS accounts, so the account id is
  # part of the name. Without it, "shahdra-recall-us-east-1-uploads" could already
  # be taken by a stranger and the apply would fail with a confusing 409.
  bucket = "${local.name_prefix}-uploads-${data.aws_caller_identity.current.account_id}"

  # force_destroy lets `terraform destroy` empty the bucket and delete it. Both
  # halves are needed: versioning is enabled below, so even after deleting every
  # object the bucket still holds old versions and delete markers, and a
  # BucketNotEmpty error would fail the destroy at the very last step.
  #
  # Unlike prevent_destroy this is an ordinary argument, so it CAN be variable-
  # driven if the guard is ever wanted back — `var.uploads_force_destroy` or
  # similar. It is a literal here to match the prevent_destroy lines, which cannot
  # be.
  force_destroy = true

  # Off so the stack is destroyable. Note this bucket is the least dangerous of the
  # four: the objects are archived source material that nothing reads back
  # (app.py never GETs them), so losing them costs re-uploading a file, not review
  # history. The DynamoDB tables are where the irreplaceable data is.
  lifecycle {
    prevent_destroy = false
  }

  tags = {
    Name = "${local.name_prefix}-uploads"
  }
}

data "aws_caller_identity" "current" {}

# Learner-uploaded course material — someone's lecture notes. Nothing about it
# should ever be world-readable, and the bucket is only ever written by the agent.
resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Encryption at rest with an SSE-S3 key rather than a customer-managed KMS key: a
# CMK adds per-request KMS charges and a second permission (kms:GenerateDataKey) on
# the app's IAM policy, to protect files whose only copy of anything sensitive is
# already in DynamoDB under the same account.
resource "aws_s3_bucket_server_side_encryption_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }

    # Cuts KMS/S3 key-derivation overhead on every PUT. Harmless with AES256, and
    # correct to set either way.
    bucket_key_enabled = true
  }
}

# Keys embed a uuid, so a re-upload never overwrites an earlier object and
# versioning is not protecting against clobbering. It protects against a mistaken
# bulk delete, which in a shared account is the likelier accident.
resource "aws_s3_bucket_versioning" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  # Versioning is on, so the bucket must not be left to accumulate: without
  # expiration rules an old version is kept forever and billed forever.
  depends_on = [aws_s3_bucket_versioning.uploads]

  rule {
    id     = "expire-uploads"
    status = "Enabled"

    filter {
      prefix = "uploads/"
    }

    expiration {
      days = var.uploads_retention_days
    }

    # Deleting a versioned object leaves a delete marker and the old version
    # behind. This is the half people forget, and it is why "I set expiration" and
    # "the storage bill went down" are not the same statement.
    noncurrent_version_expiration {
      noncurrent_days = 7
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    # A multipart upload interrupted halfway leaves parts that are invisible to
    # `aws s3 ls` and billed indefinitely.
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}
