# Outputs printed after terraform apply — useful for verifying resources and updating local configs.

output "instance_id" {
  description = "EC2 instance ID — needed for terraform import and AWS Console lookups"
  value       = aws_instance.pipeline.id
}

output "elastic_ip" {
  description = "Static public IP — update ~/.ssh/config HostName if the EIP is ever rebuilt"
  value       = aws_eip.pipeline_eip.public_ip
}

output "ecr_repository_url" {
  description = "Full ECR image URL — the base goes into .env.deploy as ECR_REGISTRY"
  value       = aws_ecr_repository.flask_app.repository_url
}

output "ami_used" {
  description = "Ubuntu 24.04 AMI ID that Terraform selected — record this for disaster recovery notes"
  value       = data.aws_ami.ubuntu_24_04.id
}

output "ssh_connect_command" {
  description = "SSH command to connect to the instance (or just: ssh ec2-stock)"
  value       = "ssh -i ~/path/to/${var.key_pair_name}.pem ubuntu@${aws_eip.pipeline_eip.public_ip}"
}
