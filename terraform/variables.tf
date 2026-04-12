# Input variables — copy terraform.tfvars.example to terraform.tfvars and fill in real values.
# terraform.tfvars is gitignored; never commit it.

variable "aws_region" {
  description = "AWS region where all resources live"
  type        = string
  default     = "us-east-1"
}

variable "key_pair_name" {
  description = "EC2 key pair name as it appears in the AWS Console (not the .pem filename)"
  type        = string
  default     = "kafkaProjectKeyPair_4-29-2025"
}

variable "ssh_ingress_cidr" {
  description = "Your current public IP in CIDR notation (e.g. 203.0.113.42/32) — only this IP can SSH in"
  type        = string
  # No default — changes per location; passed automatically by terraform.sh via curl ifconfig.me
}

variable "ssh_public_key" {
  description = "Public key material for the EC2 key pair (e.g. 'ssh-rsa AAAA...'). Passed automatically by terraform.sh via ssh-keygen -y from the .pem in ~/.ssh/config."
  type        = string
  # No default — extracted at runtime by terraform.sh; never stored in version control
}
