# Codifies existing AWS infrastructure so it can be reproduced with one terraform apply.
# Run import commands (see docs/architecture/TERRAFORM_IaC.md) to link existing resources to this state.

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── Provider ──────────────────────────────────────────────────────────────────

provider "aws" {
  region = var.aws_region
}

# ── Caller identity ───────────────────────────────────────────────────────────

# Fetches the AWS account ID at plan time — avoids requiring it as a variable input.
data "aws_caller_identity" "current" {}

# ── AMI lookup ────────────────────────────────────────────────────────────────

# Dynamically finds the latest Ubuntu 24.04 LTS AMI from Canonical — avoids hardcoding AMI IDs.
data "aws_ami" "ubuntu_24_04" {
  most_recent = true
  owners      = ["099720109477"] # Canonical's official AWS account ID

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }

  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ── Security Group ────────────────────────────────────────────────────────────

# SSH-only security group — app ports (Airflow, Dashboard, MLflow) are accessed via SSH tunnel, not exposed publicly.
resource "aws_security_group" "pipeline_sg" {
  name        = "pipeline-sg"
  description = "SSH-only ingress; all app ports accessed via SSH tunnel"

  ingress {
    description = "SSH from operators current IP only"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.ssh_ingress_cidr]
  }

  egress {
    description = "Allow all outbound (ECR pulls, apt, SEC EDGAR API calls)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name    = "pipeline-sg"
    Project = "data-pipeline"
  }
}

# ── SSH Key Pair ──────────────────────────────────────────────────────────────

# Registers the public key in AWS so new instances get it injected into authorized_keys at boot — matches the local .pem in ~/.ssh/config.
resource "aws_key_pair" "pipeline" {
  key_name   = var.key_pair_name
  public_key = var.ssh_public_key

  tags = { Project = "data-pipeline" }
}

# ── IAM Role ──────────────────────────────────────────────────────────────────

# IAM role lets EC2 authenticate to ECR via instance metadata — no stored credentials needed.
resource "aws_iam_role" "ec2_ecr_role" {
  name = "ec2-ecr-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })

  tags = { Project = "data-pipeline" }
}

# Grants push/pull access to ECR — used by flask.sh to build and deploy the dashboard image.
resource "aws_iam_role_policy_attachment" "ecr_power_user" {
  role       = aws_iam_role.ec2_ecr_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
}

# Wraps the IAM role so it can be attached to the EC2 instance.
resource "aws_iam_instance_profile" "ec2_ecr_profile" {
  name = "ec2-ecr-role"  # matches the console-auto-created profile name — imported rather than recreated
  role = aws_iam_role.ec2_ecr_role.name
}

# ── EC2 Instance ──────────────────────────────────────────────────────────────

# Single t3.large running K3s with Airflow, Kafka, MLflow, Flask, and MariaDB.
resource "aws_instance" "pipeline" {
  ami                    = data.aws_ami.ubuntu_24_04.id
  instance_type          = "t3.large"
  key_name               = aws_key_pair.pipeline.key_name  # implicit dependency — key pair must exist in AWS before instance is created
  vpc_security_group_ids = [aws_security_group.pipeline_sg.id]
  iam_instance_profile   = aws_iam_instance_profile.ec2_ecr_profile.name

  root_block_device {
    volume_type           = "gp3"
    volume_size           = 100   # GiB — sized for K3s images, MLflow artifacts, and MariaDB data
    encrypted             = true  # Free; zero performance impact — encrypts all data at rest on the volume
    delete_on_termination = false # Preserve root EBS volume if instance is destroyed — prevents permanent data loss
  }

  # user_data is omitted — bootstrap_ec2.sh handles all provisioning via SSH after the instance starts.

  lifecycle {
    # AMI updates require deliberate instance replacement, not automatic drift correction on every apply.
    ignore_changes = [ami]
  }

  tags = {
    Name    = "data-pipeline-ec2"
    Project = "data-pipeline"
  }
}

# ── Elastic IP ────────────────────────────────────────────────────────────────

# Static public IP so ~/.ssh/config and deploy configs never need updating after a stop/start.
resource "aws_eip" "pipeline_eip" {
  domain = "vpc"

  tags = {
    Name    = "pipeline-eip"
    Project = "data-pipeline"
  }
}

# Binds the Elastic IP to the EC2 instance.
resource "aws_eip_association" "pipeline_eip_assoc" {
  instance_id   = aws_instance.pipeline.id
  allocation_id = aws_eip.pipeline_eip.id
}

# ── ECR Repository ────────────────────────────────────────────────────────────

# Private ECR registry for the Flask dashboard image — K3s pulls from here on every deploy.
resource "aws_ecr_repository" "flask_app" {
  name                 = "my-flask-app"
  image_tag_mutability = "MUTABLE" # deploy.sh overwrites :latest on every build

  image_scanning_configuration {
    scan_on_push = true # Free ECR basic scanning — flags known CVEs in the Flask image on every push
  }

  tags = { Project = "data-pipeline" }
}

# Automatically removes untagged images after 1 day — every deploy overwrites :latest, leaving the old image untagged.
resource "aws_ecr_lifecycle_policy" "flask_app_lifecycle" {
  repository = aws_ecr_repository.flask_app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Remove untagged images after 1 day"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 1
      }
      action = { type = "expire" }
    }]
  })
}
