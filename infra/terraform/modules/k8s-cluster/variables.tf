# Inputs for the k8s-cluster module.
#
# No defaults on the identity/networking inputs: a missing value should be a loud
# error at plan time, not a silent deploy into the wrong VPC.

variable "cluster_name" {
  description = "Cluster identity. Prefixes every resource name and the SSM parameter path."
  type        = string
}

variable "region" {
  description = "AWS region. Passed explicitly into user-data so the in-instance AWS CLI targets the right endpoint."
  type        = string
}

variable "vpc_id" {
  description = "VPC the cluster lives in."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC CIDR. Used for the 'allow all intra-VPC traffic' security group rules."
  type        = string
}

variable "subnet_ids" {
  description = "Public subnet IDs (>= 2, in different AZs). Control plane lands in the first; the ASG spans all."
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "At least 2 subnets in different AZs are required."
  }
}

variable "control_plane_instance_type" {
  description = "EC2 instance type for the control plane."
  type        = string
}

variable "worker_instance_type" {
  description = "EC2 instance type for worker nodes."
  type        = string
}

variable "worker_desired_capacity" {
  description = "Desired worker count. 0 parks the cluster with no workers."
  type        = number
}

variable "root_volume_size" {
  description = "Root EBS volume size in GiB."
  type        = number
}

variable "kubernetes_version" {
  description = "Kubernetes minor version series for the pkgs.k8s.io apt repo, e.g. v1.30. Minor only — there is no per-patch repo."
  type        = string
}

variable "pod_network_cidr" {
  description = "Pod network CIDR for kubeadm init. Must match Calico's ipPool."
  type        = string
}

variable "ssh_key_name" {
  description = "Pre-existing EC2 key pair name for SSH access."
  type        = string
}

variable "ssh_ingress_cidr" {
  description = "CIDR allowed to reach SSH (22) and the Kubernetes API (6443)."
  type        = string
}

variable "owner" {
  description = <<-EOT
    Owner tag value, repeated onto ASG-launched instances. Terraform's provider-level
    `default_tags` do NOT propagate through an Auto Scaling Group to the instances it
    launches, so cost attribution in the shared course account needs this explicitly.
  EOT
  type        = string
}

variable "worker_min_size" {
  description = <<-EOT
    ASG minimum size. Note this interacts with worker_desired_capacity: the ASG
    honours a desired of 0, but a min_size above 0 means the next `terraform apply`
    raises it back. Keep min_size at 0 if you want `-var worker_desired_capacity=0`
    to park the cluster durably.
  EOT
  type        = number
  default     = 1
}

variable "worker_max_size" {
  description = "ASG maximum size. Caps a runaway scale-out in a shared account."
  type        = number
  default     = 3
}
