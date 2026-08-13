# Recall — AWS resources as code.
#
# Two layers in one root module:
#
#   1. Data plane — three DynamoDB tables, the uploads bucket, the reminder SNS
#      topic, and a least-privilege IAM user whose keys the pods use.
#   2. Compute — a VPC and a kubeadm Kubernetes cluster on EC2 (one control plane
#      plus a worker ASG) for the pods to run on.
#
# They are one module rather than two because the cluster is useless without the
# tables and vice versa, and a single `apply` that produces a working system is
# worth more here than the blast-radius isolation two states would buy.
#
# ── Terraform is NOT the whole story ───────────────────────────────────────
# `terraform apply` leaves you with nodes that report NotReady forever, because
# nothing has installed a CNI. infra/k8s/bootstrap.sh is the second half: Calico,
# the EBS CSI driver, namespaces, the recall-secrets Secret, ArgoCD, and the ArgoCD
# Applications. Run it over SSH after apply — see RUNBOOK.md.
#
# ── The workspace is the REGION ────────────────────────────────────────────
# Not the environment. Both dev and prod are Kubernetes namespaces on ONE cluster
# reading ONE set of tables; separating them at the AWS layer would mean six tables,
# two buckets, and two clusters for a course project that needs none of it.
#
#   terraform workspace select us-east-1
#   terraform apply -var-file=tfvars/us-east-1.tfvars
#
# ── Cost and the budget keeper ─────────────────────────────────────────────
# The data plane is nearly free at rest. The cluster is not: two t3.medium
# instances bill by the hour. A course Lambda (aws-learning-budget-keeper-function)
# also STOPS every EC2 instance in this account at 16:00 and 00:00 daily, and a
# stopped control plane cannot be restarted — kubeadm baked its public IP into the
# API server certificate, so a new IP fails TLS verification. Expect to destroy and
# re-apply rather than stop and start. RUNBOOK.md has both procedures.

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

# --- Networking -------------------------------------------------------------
#
# AZs actually available in whatever region the provider points at — never
# hard-coded, so the config is portable across regions.
data "aws_availability_zones" "available" {
  state = "available"
}

# The community VPC module builds subnets, route tables, and the internet gateway
# from one block. Using it rather than hand-rolling ~10 resources is the same call
# the reference project made, and it is a well-audited module.
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.8.1"

  name = "${local.name_prefix}-vpc"
  cidr = var.vpc_cidr

  # slice(...,0,2) takes the first two AZs, giving two public subnets in different
  # Availability Zones without naming any region-specific AZ.
  azs = slice(data.aws_availability_zones.available.names, 0, 2)

  # cidrsubnet() derives the ranges from var.vpc_cidr, so changing the VPC CIDR does
  # not require editing subnet literals.
  # 10.0.0.0/16 -> 10.0.101.0/24, 10.0.102.0/24
  public_subnets = [
    cidrsubnet(var.vpc_cidr, 8, 101),
    cidrsubnet(var.vpc_cidr, 8, 102),
  ]

  # No private subnets, which also means no NAT gateway. A NAT would add ~$32/month
  # for no benefit here: every node needs outbound internet (apt, image pulls,
  # Bedrock) and inbound NodePort access, so they belong in public subnets.
  private_subnets    = []
  enable_nat_gateway = false

  # Workers launched by the ASG need public IPs to be reachable on NodePorts and to
  # pull images.
  map_public_ip_on_launch = true

  # kubelet registers nodes by their private DNS name
  # (ip-10-0-x-y.<region>.compute.internal); both of these must be on for that to
  # resolve.
  enable_dns_hostnames = true
  enable_dns_support   = true
}

# --- The cluster ------------------------------------------------------------
module "k8s_cluster" {
  source = "./modules/k8s-cluster"

  cluster_name = local.name_prefix
  region       = var.region
  owner        = var.owner

  vpc_id     = module.vpc.vpc_id
  vpc_cidr   = var.vpc_cidr
  subnet_ids = module.vpc.public_subnets

  control_plane_instance_type = var.control_plane_instance_type
  worker_instance_type        = var.worker_instance_type
  worker_desired_capacity     = var.worker_desired_capacity
  root_volume_size            = var.root_volume_size

  kubernetes_version = var.kubernetes_version
  pod_network_cidr   = var.pod_network_cidr

  ssh_key_name     = var.ssh_key_name
  ssh_ingress_cidr = var.ssh_ingress_cidr
}
