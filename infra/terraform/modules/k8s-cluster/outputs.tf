# Module outputs. Re-exported by the root module (../../outputs.tf), which is what
# the runbook's commands actually read.

output "control_plane_public_ip" {
  description = "Public IP of the control plane. SSH here to run bootstrap.sh."
  value       = aws_instance.control_plane.public_ip
}

output "control_plane_private_ip" {
  description = "Private IP of the control plane (the API server's advertise address)."
  value       = aws_instance.control_plane.private_ip
}

output "control_plane_instance_id" {
  description = "Instance ID of the control plane, for aws ec2 / ssm commands."
  value       = aws_instance.control_plane.id
}

output "control_plane_security_group_id" {
  description = "Security group on the control plane. Add a temporary rule here to expose the ArgoCD UI."
  value       = aws_security_group.control_plane.id
}

output "worker_security_group_id" {
  description = "Security group on the worker nodes. Holds the Recall NodePort rules."
  value       = aws_security_group.worker.id
}

output "worker_asg_name" {
  description = "Worker Auto Scaling Group name, for scaling without a terraform apply."
  value       = aws_autoscaling_group.workers.name
}

output "join_command_ssm_parameter" {
  description = <<-EOT
    SSM parameter holding the `kubeadm join` command. Inspect it when a worker fails
    to join. NOTE: this parameter is created by the control plane at boot, NOT by
    Terraform, so `terraform destroy` does not remove it — see the runbook.
  EOT
  value       = local.join_command_param
}

output "kubeconfig_path_on_control_plane" {
  description = "Path on the control plane to a kubeconfig whose server URL is the PUBLIC ip."
  value       = "/home/ubuntu/kubeconfig-public.yaml"
}

output "ubuntu_ami_id" {
  description = "The Ubuntu 22.04 AMI resolved for this region, recorded for debugging a failed boot."
  value       = data.aws_ami.ubuntu.id
}
