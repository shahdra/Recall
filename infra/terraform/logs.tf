# Archive bucket for container logs, and the read/write permissions around it.
#
# Fluent Bit runs as a DaemonSet (infra/k8s/monitoring/fluent-bit.yaml), tails
# every container's log file on its node, tags each record with the pod's
# Kubernetes metadata, and ships gzipped NDJSON here under
# logs/YYYY/MM/DD/. services/observability-mcp reads it back.
#
# Why an archive at all, when `kubectl logs` exists: a pod's logs die with the
# pod. The failures worth investigating are disproportionately the ones that
# killed the pod — an OOM, a crashloop, a node replaced by the ASG — so by the
# time anyone looks, kubectl has nothing left to show. This bucket is what makes
# "what happened at 14:02 yesterday" answerable at all.
#
# Why not CloudWatch Logs: ingestion is billed per GB and this cluster's chatty
# components (the API server, the ingress controller) would dominate the bill
# without being what anyone reads. S3 at rest plus a lifecycle rule is roughly
# two orders of magnitude cheaper for logs that are read rarely and in bulk.

resource "aws_s3_bucket" "logs" {
  # Same account-id suffix as the uploads bucket, for the same reason: bucket
  # names are globally unique across every AWS account, so an unqualified name
  # can collide with a stranger's and fail the apply with a confusing 409.
  bucket = "${local.name_prefix}-logs-${data.aws_caller_identity.current.account_id}"

  # Logs are the most disposable data in the stack — they are a copy of what the
  # pods already printed. Making the bucket destroyable keeps `terraform destroy`
  # from stopping on a BucketNotEmpty error with thousands of objects in it.
  force_destroy = true

  lifecycle {
    prevent_destroy = false
  }

  tags = {
    Name = "${local.name_prefix}-logs"
  }
}

# Application logs carry request paths, user ids, and whatever a stack trace
# happened to include. Never world-readable.
resource "aws_s3_bucket_public_access_block" "logs" {
  bucket = aws_s3_bucket.logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }

    # Fluent Bit uploads an object per node every upload_timeout, so this bucket
    # sees far more PUTs than uploads/ does. Key-derivation overhead per request
    # is worth switching off.
    bucket_key_enabled = true
  }
}

# Deliberately NOT versioned, unlike the uploads bucket.
#
# Every key already embeds a timestamp and the node's tag, so nothing is ever
# overwritten and there is no clobbering to protect against. Versioning here
# would only add noncurrent versions to bill for, and the lifecycle rule below
# would need a second clause to clean up what versioning created.

resource "aws_s3_bucket_lifecycle_configuration" "logs" {
  bucket = aws_s3_bucket.logs.id

  rule {
    id     = "expire-logs"
    status = "Enabled"

    filter {
      prefix = "logs/"
    }

    # Logs are for debugging something that just happened, and the MCP server
    # caps a lookback at 24 hours anyway. Two weeks covers "what changed since
    # last sprint" without accumulating indefinitely.
    expiration {
      days = var.logs_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"

    filter {}

    # Fluent Bit uses PutObject for small files but multipart for larger ones, so
    # an interrupted shipper can leave billable invisible parts behind.
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
  }
}

# --- Permissions --------------------------------------------------------------
#
# The write side lives in iam.tf, in the single `app` policy document that
# modules/k8s-cluster attaches to the worker node role — the same document that
# grants DynamoDB and the uploads bucket. Keeping every application permission in
# one document is what makes "what can a pod on this node do?" answerable by
# reading one file.
#
# The read side is deliberately NOT granted to anything in the cluster.
# observability-mcp runs on a laptop over stdio and authenticates as whoever
# launched it, so the cluster never needs to read its own logs back. Granting it
# would widen what a compromised pod can reach for no operational benefit.
