# k8s-cluster — a kubeadm-provisioned Kubernetes cluster on EC2.
#
# Layout:
#   * one control-plane EC2 instance, initialised by user-data on first boot
#   * worker nodes managed by an Auto Scaling Group via a launch template
#   * SSM Parameter Store carries the `kubeadm join` command from the control plane
#     to workers (see "How workers join" below)
#
# Adapted from the reference project's module of the same name. The departures are
# marked "RECALL:" and are the NodePort rule (narrowed to the four ports Recall
# actually serves) and the owner tag input.
#
# ── How workers join the cluster ────────────────────────────────────────────
# The ASG launches workers at unpredictable times - possibly minutes after the
# control plane, possibly days later on a scale-out - so the join command cannot be
# baked into the launch template at apply time (it does not exist yet, and kubeadm
# tokens expire).
#
# Chosen solution: SSM Parameter Store as a one-way channel.
#   1. Control-plane user-data runs `kubeadm init`, then
#      `kubeadm token create --print-join-command`, and writes the result to a
#      SecureString parameter.
#   2. Worker user-data polls that parameter until it exists, then evals it.
#   3. A cron on the control plane refreshes the token every 12h, so a scale-out
#      next week still gets a valid token (kubeadm tokens default to a 24h TTL).
#
# Why this over the alternatives:
#   * vs. Lambda + ASG lifecycle hooks - no Lambda, no SNS topic, no IAM plumbing
#     between them; the whole mechanism is two AWS CLI calls you can run by hand.
#   * vs. Secrets Manager - functionally identical, but SSM SecureString is free at
#     this scale while Secrets Manager bills per secret per month.
#   * Security: IAM scopes the control plane to PutParameter and workers to
#     GetParameter on this cluster's path only, so neither can read another
#     student's parameters in this shared account.
#
# Node removal on scale-down is MANUAL (`kubectl delete node <name>`). A
# terminating-lifecycle-hook + Lambda would automate it, at the cost of machinery
# that is harder to explain than the manual step it replaces.

# Newest Ubuntu 22.04 LTS AMI in the CURRENT region (owner = Canonical). A data
# lookup rather than a hard-coded AMI ID is what makes this module work in any
# region unchanged.
data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

locals {
  # SSM parameter path carrying the kubeadm join command from the control plane to
  # ASG-launched workers. The control plane writes it; workers read it.
  #
  # Only ONE parameter is used. Publishing the kubeconfig here too would fail: a
  # kubeconfig is ~5.4 KB and SSM Standard-tier parameters cap at 4096 characters.
  # It would also put cluster-admin certs in a shared account. The control plane
  # writes that file to /home/ubuntu/kubeconfig-public.yaml on disk instead.
  join_command_param = "/recall/${var.cluster_name}/join-command"
}

# ---------------------------------------------------------------------------
# IAM — shared trust policy
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ec2_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------------------
# IAM — control plane
# ---------------------------------------------------------------------------
resource "aws_iam_role" "control_plane" {
  name               = "${var.cluster_name}-control-plane"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "control_plane_ebs_csi" {
  role = aws_iam_role.control_plane.name
  # Lets the EBS CSI controller create/attach/delete volumes. Nothing in Recall
  # uses a PersistentVolumeClaim today - all four workloads are stateless, with
  # state in DynamoDB - but the driver is installed by bootstrap.sh so that adding
  # Prometheus or Grafana later does not require touching IAM.
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role_policy_attachment" "control_plane_ecr" {
  role       = aws_iam_role.control_plane.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# Not strictly required, but it enables `aws ssm start-session` for shell access
# without SSH - invaluable when a user-data script fails and the instance never
# opens port 22.
resource "aws_iam_role_policy_attachment" "control_plane_ssm_core" {
  role       = aws_iam_role.control_plane.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Least privilege: the control plane may write ONLY its own cluster's parameter.
data "aws_iam_policy_document" "control_plane_ssm_write" {
  statement {
    sid = "ManageOwnClusterParameters"
    actions = [
      "ssm:PutParameter",
      "ssm:GetParameter",
      "ssm:DeleteParameter",
      "ssm:AddTagsToResource",
    ]
    resources = [
      "arn:aws:ssm:${var.region}:*:parameter${local.join_command_param}",
    ]
  }
}

resource "aws_iam_role_policy" "control_plane_ssm_write" {
  name   = "${var.cluster_name}-control-plane-ssm"
  role   = aws_iam_role.control_plane.id
  policy = data.aws_iam_policy_document.control_plane_ssm_write.json
}

resource "aws_iam_instance_profile" "control_plane" {
  name = "${var.cluster_name}-control-plane"
  role = aws_iam_role.control_plane.name
}

# ---------------------------------------------------------------------------
# IAM — workers
# ---------------------------------------------------------------------------
#
# HOW APPLICATION PODS GET AWS CREDENTIALS.
#
# They inherit this NODE role through the instance metadata service. boto3's default
# credential chain finds it with no configuration and no code change - nothing in
# services/ reads AWS_ACCESS_KEY_ID explicitly - and the credentials it hands out are
# short-lived and rotated by AWS.
#
# The alternative, which this deliberately replaced, was static access keys for a
# dedicated IAM user, delivered through the `recall-secrets` Secret. That works, and
# it scopes access per-workload rather than per-node, but it created a treadmill:
# `terraform destroy` deletes the user, so its keys die with every teardown and every
# consumer of them (a GitHub secret, a local env file) goes stale on a cycle this
# project runs nightly. Keys that must be re-minted and re-pasted on each apply get
# re-pasted wrong.
#
# WHAT THIS GIVES UP: the role is attached to the INSTANCE, so every pod scheduled on
# a worker gets these permissions, not just Recall's four. Pod-level scoping is what
# IRSA provides, and IRSA needs an OIDC provider that a kubeadm cluster does not have.
# On a single-tenant lab cluster that is destroyed nightly the distinction costs
# nothing; on a shared production cluster it would be unacceptable.
#
# The policy itself is unchanged and still least-privilege - the same document that
# scoped the IAM user now scopes this role. It is passed in from the root module
# (see iam.tf) rather than defined here, because every ARN in it belongs to a
# data-plane resource the root module owns.
resource "aws_iam_role" "worker" {
  name               = "${var.cluster_name}-worker"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume_role.json
}

resource "aws_iam_role_policy_attachment" "worker_ecr" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_role_policy_attachment" "worker_ebs_csi" {
  role = aws_iam_role.worker.name
  # The EBS CSI *node* plugin runs on workers and needs DescribeVolumes /
  # AttachVolume to mount any future PersistentVolume.
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_iam_role_policy_attachment" "worker_ssm_core" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Workers may READ the join command, and publish alerts.
data "aws_iam_policy_document" "worker_ssm_read" {
  statement {
    sid       = "ReadJoinCommand"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:*:parameter${local.join_command_param}"]
  }

  # Alertmanager runs on a worker and publishes alerts to SNS using this role's
  # credentials via the instance metadata service — there is no IRSA on a kubeadm
  # cluster, so a pod cannot assume a role of its own. Scoped to the alerts topic
  # ARN alone: the account is shared, and a wildcard would let anything on a node
  # publish to a classmate's topic.
  statement {
    sid       = "PublishAlerts"
    actions   = ["sns:Publish"]
    resources = [var.alerts_topic_arn]
  }
}

resource "aws_iam_role_policy" "worker_ssm_read" {
  name   = "${var.cluster_name}-worker-ssm"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker_ssm_read.json
}

# The application's own permissions: DynamoDB on the three tables and the GSI, S3
# under uploads/, SNS publish, and Bedrock InvokeModel on two models. Every statement
# is scoped to a named ARN - see iam.tf, which builds this document.
#
# Attached as an inline policy rather than a managed one so it cannot be attached to
# anything else by accident, and so it is deleted with the role.
resource "aws_iam_role_policy" "worker_app" {
  name   = "${var.cluster_name}-worker-app"
  role   = aws_iam_role.worker.id
  policy = var.app_policy_json
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.cluster_name}-worker"
  role = aws_iam_role.worker.name
}

# ---------------------------------------------------------------------------
# Security groups
# ---------------------------------------------------------------------------
resource "aws_security_group" "control_plane" {
  name        = "${var.cluster_name}-control-plane"
  description = "Kubernetes control plane: SSH, API server, and all intra-VPC traffic"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.cluster_name}-control-plane" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_ssh" {
  security_group_id = aws_security_group.control_plane.id
  description       = "SSH - needed to run bootstrap.sh, which installs the CNI"
  cidr_ipv4         = var.ssh_ingress_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_apiserver" {
  security_group_id = aws_security_group.control_plane.id
  description       = "Kubernetes API server - reached by kubectl from your laptop"
  cidr_ipv4         = var.ssh_ingress_cidr
  from_port         = 6443
  to_port           = 6443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "control_plane_intra_vpc" {
  security_group_id = aws_security_group.control_plane.id
  # One rule for all intra-VPC traffic. It covers kubelet (10250), etcd
  # (2379-2380), the NodePort range, and Calico's BGP/VXLAN/IPIP without
  # enumerating them - enumeration is where CNI setups silently break.
  description = "All traffic from within the VPC"
  cidr_ipv4   = var.vpc_cidr
  ip_protocol = "-1"
}

resource "aws_vpc_security_group_egress_rule" "control_plane_all" {
  security_group_id = aws_security_group.control_plane.id
  description       = "All outbound - apt, image pulls, AWS APIs"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "worker" {
  name        = "${var.cluster_name}-worker"
  description = "Kubernetes workers: SSH, Recall NodePorts, and all intra-VPC traffic"
  vpc_id      = var.vpc_id

  tags = { Name = "${var.cluster_name}-worker" }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "worker_ssh" {
  security_group_id = aws_security_group.worker.id
  description       = "SSH"
  cidr_ipv4         = var.ssh_ingress_cidr
  from_port         = 22
  to_port           = 22
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "worker_intra_vpc" {
  security_group_id = aws_security_group.worker.id
  description       = "All traffic from within the VPC"
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "-1"
}

# RECALL: the reference opens the entire NodePort range (30000-32767) to the world.
# Recall serves exactly four ports, so only those are open:
#
#   30300 dev frontend    30800 dev tutor-agent
#   31300 prod frontend   31800 prod tutor-agent
#
# Two contiguous pairs means two rules rather than four. The browser must reach
# BOTH ports of a pair: frontend/lib/api.ts derives the agent URL client-side as
# frontendPort + 500, so the API call comes from the user's browser, not from
# inside the cluster. Opening only the frontend port would render a working page
# whose every request fails.
#
# study-mcp is deliberately absent - it is ClusterIP, reached only by tutor-agent
# inside the cluster, and exposing it would publish an unauthenticated tool API.
resource "aws_vpc_security_group_ingress_rule" "worker_nodeports_dev" {
  security_group_id = aws_security_group.worker.id
  description       = "Recall dev NodePorts: 30300 frontend, 30800 tutor-agent"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 30300
  to_port           = 30800
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "worker_nodeports_prod" {
  security_group_id = aws_security_group.worker.id
  description       = "Recall prod NodePorts: 31300 frontend, 31800 tutor-agent"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 31300
  to_port           = 31800
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "worker_all" {
  security_group_id = aws_security_group.worker.id
  description       = "All outbound - apt, image pulls, AWS APIs, Bedrock"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# ---------------------------------------------------------------------------
# Control plane instance
# ---------------------------------------------------------------------------
resource "aws_instance" "control_plane" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = var.control_plane_instance_type

  subnet_id                   = var.subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.control_plane.id]
  iam_instance_profile        = aws_iam_instance_profile.control_plane.name
  key_name                    = var.ssh_key_name
  associate_public_ip_address = true

  root_block_device {
    volume_size           = var.root_volume_size
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/templates/control-plane-user-data.sh.tftpl", {
    cluster_name       = var.cluster_name
    region             = var.region
    kubernetes_version = var.kubernetes_version
    pod_network_cidr   = var.pod_network_cidr
    join_command_param = local.join_command_param
  })

  # user-data only executes on FIRST boot, so editing the script must replace the
  # instance for the change to take effect. Without this, a script edit produces a
  # successful apply that changes nothing on the box - a genuinely confusing
  # failure mode.
  user_data_replace_on_change = true

  # IAM must exist before boot: user-data's first AWS call is a DeleteParameter,
  # and an instance profile attached a second late means a hard failure.
  depends_on = [
    aws_iam_role_policy.control_plane_ssm_write,
    aws_iam_role_policy_attachment.control_plane_ssm_core,
  ]

  tags = {
    Name = "${var.cluster_name}-control-plane"
    Role = "control-plane"
    # Cluster is what bootstrap.sh reads to derive the alerts SNS topic ARN when one
    # is not passed in. The worker ASG propagates this tag to its instances, but a
    # plain aws_instance has no such mechanism — so omitting it here left the control
    # plane, the very node bootstrap runs ON, unable to identify its own cluster.
    Cluster = var.cluster_name
  }
}

# ---------------------------------------------------------------------------
# Workers — launch template + ASG
# ---------------------------------------------------------------------------
resource "aws_launch_template" "worker" {
  name_prefix   = "${var.cluster_name}-worker-"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = var.worker_instance_type
  key_name      = var.ssh_key_name

  vpc_security_group_ids = [aws_security_group.worker.id]

  iam_instance_profile {
    name = aws_iam_instance_profile.worker.name
  }

  block_device_mappings {
    device_name = "/dev/sda1" # Ubuntu's root device
    ebs {
      volume_size           = var.root_volume_size
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_tokens                 = "required" # IMDSv2 only
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 2 # containers may need IMDS
  }

  user_data = base64encode(templatefile("${path.module}/templates/worker-user-data.sh.tftpl", {
    cluster_name       = var.cluster_name
    region             = var.region
    kubernetes_version = var.kubernetes_version
    join_command_param = local.join_command_param
  }))

  # Launch-template tags do NOT propagate to launched instances; tag_specifications
  # is what actually labels the EC2 instances the ASG creates.
  tag_specifications {
    resource_type = "instance"
    tags = {
      Name = "${var.cluster_name}-worker"
      Role = "worker"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "${var.cluster_name}-worker-root"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "workers" {
  name = "${var.cluster_name}-workers"

  vpc_zone_identifier = var.subnet_ids # spans both AZs

  min_size         = var.worker_min_size
  max_size         = var.worker_max_size
  desired_capacity = var.worker_desired_capacity

  launch_template {
    id      = aws_launch_template.worker.id
    version = "$Latest"
  }

  # EC2 health checks only. An ELB health check would be wrong here: nothing fronts
  # these nodes with a load balancer, and NodePort readiness depends on pod
  # scheduling rather than instance health.
  health_check_type         = "EC2"
  health_check_grace_period = 300 # allow time for deps install + kubeadm join

  # Wait for the control plane before launching workers. The worker's SSM poll loop
  # makes it eventually-correct regardless, but this avoids a worker burning 5
  # minutes polling for a parameter that does not exist yet.
  depends_on = [aws_instance.control_plane]

  # Editing worker user-data should recycle the fleet, not leave stale nodes.
  instance_refresh {
    strategy = "Rolling"
    preferences {
      # 0% ensures a 1-node ASG can still refresh (it terminates then replaces).
      min_healthy_percentage = 0
    }
  }

  tag {
    key                 = "Name"
    value               = "${var.cluster_name}-worker"
    propagate_at_launch = true
  }

  tag {
    key                 = "Role"
    value               = "worker"
    propagate_at_launch = true
  }

  # Terraform's provider-level default_tags do not reach ASG-launched instances.
  # Repeat the ones that matter for cost attribution and for finding our own
  # instances in a shared account.
  tag {
    key                 = "Owner"
    value               = var.owner
    propagate_at_launch = true
  }

  tag {
    key                 = "Project"
    value               = "recall"
    propagate_at_launch = true
  }

  tag {
    key                 = "Cluster"
    value               = var.cluster_name
    propagate_at_launch = true
  }

  # NOTE on desired_capacity and manual scaling.
  # desired_capacity is deliberately NOT in ignore_changes, so scaling by hand with
  #   aws autoscaling set-desired-capacity ... --desired-capacity 0
  # is reverted by the next `terraform apply`. That is visible in the plan output.
  # To park the cluster durably, apply with `-var worker_desired_capacity=0`.
}
