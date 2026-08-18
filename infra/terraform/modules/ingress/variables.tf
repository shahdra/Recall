# Inputs for the ingress module.
#
# No defaults on the identity/networking inputs: a missing value should be a loud
# error at plan time, not an ALB pointed at the wrong VPC.

variable "cluster_name" {
  description = "Cluster identity; prefixes every resource name in this module."
  type        = string
}

variable "vpc_id" {
  description = "VPC the ALB and the worker nodes live in."
  type        = string
}

variable "subnet_ids" {
  description = <<-EOT
    Public subnets for the ALB. An Application Load Balancer requires at least two
    in different Availability Zones — AWS rejects a single-subnet ALB — which is
    why the VPC module creates two even though one worker runs in one of them.
  EOT
  type        = list(string)

  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "An ALB needs at least 2 subnets in different AZs."
  }
}

variable "worker_asg_name" {
  description = <<-EOT
    Worker Auto Scaling Group to attach the target group to. This attachment is
    what registers new workers automatically, so a replaced instance does not take
    the site down until someone notices.
  EOT
  type        = string
}

variable "worker_security_group_id" {
  description = <<-EOT
    Security group on the worker nodes. Gets a rule allowing the ALB in on the
    ingress NodePort — required, not optional, because Recall's worker SG opens
    only the app's own NodePort ranges and 30080 is outside them.
  EOT
  type        = string
}

variable "ingress_http_node_port" {
  description = <<-EOT
    NodePort the ingress-nginx controller's HTTP listener is pinned to, and the port
    the ALB target group forwards to.

    MUST match `controller.service.nodePorts.http` in
    infra/k8s/ingress-nginx/values.yaml. bootstrap.sh asserts the two agree after
    installing the chart, because a mismatch produces a healthy-looking cluster
    that returns 503 for every hostname with nothing in any pod log to explain it.

    Pinned rather than left to Kubernetes' random allocation because the Terraform
    target group has to know the port before the chart is ever installed.
  EOT
  type        = number
  default     = 30080
}

variable "base_domain" {
  description = <<-EOT
    The SHARED hosted zone, read with a data source and never managed here. Every
    student in this account has records in it, so `terraform destroy` must remove
    only our own — declaring the zone as a resource would delete everyone's.
  EOT
  type        = string
  default     = "fursa.click"
}

variable "subdomain" {
  description = <<-EOT
    This project's label under `base_domain`, giving a domain root of
    "<subdomain>.<base_domain>" — recall.fursa.click.

    Deliberately NOT the student name: the reference project on this same account
    and hosted zone uses that, and sharing it would mean whichever stack applied
    second repointed the other's records at its own ALB, with both ACM
    certificates competing over identical validation CNAMEs.
  EOT
  type        = string
  default     = "recall"
}

variable "hosts" {
  description = <<-EOT
    Names to create Route 53 alias records for, relative to the domain root. ""
    (empty string) means the root itself. Each becomes an A-record ALIAS pointing at
    the ALB, which is what makes every Ingress host actually resolvable — an Ingress
    manifest alone routes nothing if DNS does not arrive at the ALB first.
  EOT
  type        = list(string)
  default = [
    "",             # recall.fursa.click         -> prod frontend + tutor-agent
    "dev",          # dev.recall.fursa.click     -> dev frontend + tutor-agent
    "argocd",       # argocd.recall.fursa.click
    "grafana",      # grafana.recall.fursa.click
    "prometheus",   # prometheus.recall.fursa.click
    "alertmanager", # alertmanager.recall.fursa.click
  ]
}
