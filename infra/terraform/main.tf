# Recall — AWS resources as code.
#
# Scope is deliberately narrow: the three DynamoDB tables, the uploads bucket, the
# reminder SNS topic, and an IAM user for the pods. It does NOT provision a
# Kubernetes cluster — Recall deploys into `dev` and `prod` namespaces on the
# existing cluster, so standing up a second one would duplicate ~26 resources in a
# shared account for no benefit.
#
# The WORKSPACE identifies the REGION, not the environment (a convention carried
# over from the cluster's own Terraform). Both dev and prod read the same tables:
# separating them would mean six tables and a second bucket, which the course
# project does not need. Environment separation is at the Kubernetes layer.
#
#   terraform workspace select us-east-1
#   terraform plan -var-file=tfvars/us-east-1.tfvars
#
# NOTE: `terraform apply` requires instructor approval before it is run — the
# account is shared with the course (docs/plan.md:827). validate + plan only.

terraform {
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source = "hashicorp/aws"

      # Pinned to the v6 major line, not just a ">= 5.55" floor. An open floor
      # resolves to whatever is newest at `init` time: this config was first
      # written against the reference project's ">= 5.55" and silently installed
      # v6.59, where `range_key` on aws_dynamodb_table is deprecated. Capping the
      # major version means a future v7 requires a deliberate bump here rather
      # than surprising the next person to run init.
      version = "~> 6.0"
    }
  }

  # Remote state in S3, keyed per workspace (env:/<workspace>/recall.tfstate).
  #
  # NOTE: backend blocks permit no variables or interpolation — every value must
  # be a literal. The bucket must therefore exist BEFORE `terraform init`; see
  # RUNBOOK.md for the one-time creation command. A bucket managed by the state it
  # stores is a chicken-and-egg problem, which is why it is created out-of-band.
  backend "s3" {
    bucket  = "shahdra-recall-tfstate-228281126655"
    key     = "recall.tfstate"
    region  = "us-east-1"
    encrypt = true
  }
}

provider "aws" {
  region = var.region

  # No `profile` here on purpose: CI authenticates with AWS_ACCESS_KEY_ID /
  # AWS_SECRET_ACCESS_KEY environment variables, and a hard-coded profile name
  # would break it. Locally the default profile is picked up automatically.

  default_tags {
    tags = {
      Owner       = var.owner
      Project     = "recall"
      ManagedBy   = "terraform"
      TFWorkspace = terraform.workspace
    }
  }
}

locals {
  # Every resource name derives from this. Critical in a SHARED course account:
  # `aws s3 ls` shows ten classmates' state buckets, so an unprefixed name like
  # "Cards" or "recall-uploads" is a collision waiting to happen.
  name_prefix = "${var.owner}-recall-${terraform.workspace}"
}

# Guard against the likeliest operator error: applying one region's tfvars while a
# different region's workspace is selected, which would silently build a second set
# of tables whose names claim the wrong region.
#
# terraform_data is built into Terraform >= 1.4 — no extra provider needed.
resource "terraform_data" "workspace_region_guard" {
  input = "${terraform.workspace}/${var.region}"

  lifecycle {
    precondition {
      condition     = terraform.workspace == var.region
      error_message = "Workspace '${terraform.workspace}' does not match region '${var.region}'. Run: terraform workspace select ${var.region}"
    }
  }
}
