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

output "app_iam_role" {
  description = <<-EOT
    IAM role the pods' AWS access comes from. There is no IAM user and no access key
    to mint: pods inherit this role from the worker instance via the metadata service
    (see iam.tf). Inspect what they may do with:
      aws iam get-role-policy --role-name <this value> --policy-name <this value>-app
  EOT
  value       = module.k8s_cluster.worker_iam_role_name
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

# ===========================================================================
# Cluster
# ===========================================================================
#
# Re-exported from module.k8s_cluster. The runbook's commands read these, so the
# names here are what RUNBOOK.md quotes.

output "cluster_name" {
  description = <<-EOT
    Cluster identity — prefixes every resource name and the SSM parameter path. Read
    by the Provision Cluster workflow for its run summary, which is the only way to
    tell two regions' clusters apart at a glance.
  EOT
  value       = local.name_prefix
}

output "vpc_id" {
  description = "The cluster VPC."
  value       = module.vpc.vpc_id
}

output "public_subnet_ids" {
  description = "Public subnets the nodes run in (one per AZ)."
  value       = module.vpc.public_subnets
}

output "control_plane_public_ip" {
  description = "Public IP of the control plane. SSH here to run infra/k8s/bootstrap.sh."
  value       = module.k8s_cluster.control_plane_public_ip
}

output "control_plane_instance_id" {
  description = "Control-plane instance id, for `aws ec2` / `aws ssm start-session`."
  value       = module.k8s_cluster.control_plane_instance_id
}

output "worker_asg_name" {
  description = "Worker Auto Scaling Group name."
  value       = module.k8s_cluster.worker_asg_name
}

output "join_command_ssm_parameter" {
  description = <<-EOT
    SSM parameter carrying the `kubeadm join` command. Created by the control plane at
    boot, NOT by Terraform — so `terraform destroy` leaves it behind and the runbook
    deletes it by hand.
  EOT
  value       = module.k8s_cluster.join_command_ssm_parameter
}

# --- Paste-ready commands ---------------------------------------------------
#
# These exist because the control plane's IP is not known until apply finishes, so
# every one of these commands has to be assembled from an output anyway. Emitting
# them fully-formed removes the step where you paste the wrong IP.

output "ssh_command" {
  description = "SSH to the control plane."
  value       = "ssh -i ${var.ssh_private_key_path} ubuntu@${module.k8s_cluster.control_plane_public_ip}"
}

output "fetch_kubeconfig_command" {
  description = <<-EOT
    Copy the control plane's public-IP kubeconfig to your laptop. Run AFTER
    bootstrap.sh. Note it writes ~/.kube/config-recall rather than ~/.kube/config so
    it cannot clobber an existing context; use it with
    `export KUBECONFIG=~/.kube/config-recall`.
  EOT
  value       = "scp -i ${var.ssh_private_key_path} ubuntu@${module.k8s_cluster.control_plane_public_ip}:${module.k8s_cluster.kubeconfig_path_on_control_plane} ~/.kube/config-recall"
}

output "recall_urls" {
  description = <<-EOT
    Where the app is reachable once the manifests are synced. These point at the
    CONTROL PLANE's IP, which works because kube-proxy makes every NodePort answer on
    every node — convenient here since worker IPs change whenever the ASG replaces an
    instance, while the control plane's does not.
  EOT
  value = {
    dev_frontend     = "http://${module.k8s_cluster.control_plane_public_ip}:30300"
    dev_tutor_agent  = "http://${module.k8s_cluster.control_plane_public_ip}:30800"
    prod_frontend    = "http://${module.k8s_cluster.control_plane_public_ip}:31300"
    prod_tutor_agent = "http://${module.k8s_cluster.control_plane_public_ip}:31800"
  }
}
