# Module outputs. Re-exported by the root module (../../outputs.tf), and two of
# them are consumed by infra/k8s/bootstrap.sh rather than by a human: the Helm
# values file carries placeholders that bootstrap substitutes from these.

output "domain_root" {
  description = <<-EOT
    This project's domain root, e.g. "recall.fursa.click". bootstrap.sh substitutes
    it for __DOMAIN_ROOT__ in infra/k8s/monitoring/values.yaml — Prometheus and
    Grafana build absolute URLs from it, and would otherwise emit unreachable
    http://<pod-ip>:9090 links in alert emails.
  EOT
  value       = local.domain_root
}

output "alb_dns_name" {
  description = <<-EOT
    The ALB's own DNS name. Useful for debugging when a hostname does not resolve:
    curl -H 'Host: recall.fursa.click' http://<this> reaches the ingress controller
    without involving Route 53 at all, which separates a DNS problem from a routing
    problem.
  EOT
  value       = aws_lb.this.dns_name
}

output "alb_zone_id" {
  description = "Hosted zone of the ALB itself, used by the ALIAS records."
  value       = aws_lb.this.zone_id
}

output "alb_security_group_id" {
  description = "Security group on the ALB. Narrow its :443 rule to restrict who can reach the site."
  value       = aws_security_group.alb.id
}

output "target_group_arn" {
  description = <<-EOT
    Target group the worker ASG is attached to. When every hostname returns 503,
    this is the first thing to check:
      aws elbv2 describe-target-health --target-group-arn <this>
    Unhealthy targets mean the ingress controller is not answering on the NodePort.
  EOT
  value       = aws_lb_target_group.ingress_nginx.arn
}

output "certificate_arn" {
  description = "The issued ACM certificate. Reads from the validation resource, so a non-empty value means ACM finished issuing."
  value       = aws_acm_certificate_validation.this.certificate_arn
}

output "urls" {
  description = "Every published hostname, as a browsable https URL."
  value       = { for k, v in local.fqdns : (k == "" ? "prod" : k) => "https://${v}" }
}
