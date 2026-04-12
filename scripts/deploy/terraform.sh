#!/bin/bash
# Terraform wrapper — exposes init/plan/apply/destroy for the pipeline EC2 infrastructure.
# Auto-detects your current public IP and passes it as the SSH security group ingress rule.
#
# Usage (standalone):  ./scripts/deploy/terraform.sh [init|plan|apply|destroy]
# Usage (via deploy):  ./scripts/deploy.sh --provision  (calls apply automatically)
#
# To override the auto-detected IP: SSH_INGRESS_CIDR="1.2.3.4/32" ./scripts/deploy/terraform.sh apply

set -euo pipefail

# Resolve paths so this script can be called from any working directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"  # scripts/deploy → scripts → project root
TF_DIR="$PROJECT_ROOT/terraform"                      # resolves to project_root/terraform/

# Use a named SSO profile — override with AWS_PROFILE env var
export AWS_PROFILE="${AWS_PROFILE:-terraform-dev}"

# Verify terraform is installed before proceeding
if ! command -v terraform &>/dev/null; then
    echo "ERROR: terraform not found. Install from: https://developer.hashicorp.com/terraform/install"
    exit 1
fi

# Verify AWS CLI is installed before testing credentials
if ! command -v aws &>/dev/null; then
    echo "ERROR: aws CLI not found. Install from: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Print SSO profile setup instructions and exit — called when credentials are missing or expired after login attempt
_sso_setup_instructions() {
    echo "ERROR: No valid AWS credentials found for profile '$AWS_PROFILE'."
    echo ""
    echo "HOW SSO AUTH WORKS (Option B):"
    echo "  You configure a named profile in ~/.aws/config once."
    echo "  This script calls 'aws sso login' automatically when your session expires (every ~8 hours)."
    echo "  A browser window opens → you click Approve → done. No static keys stored anywhere."
    echo ""
    echo "ONE-TIME SETUP — add this block to ~/.aws/config:"
    echo ""
    echo "  [profile terraform-dev]"
    echo "  sso_start_url  = https://YOUR_SUBDOMAIN.awsapps.com/start"
    echo "  sso_region     = us-east-1"
    echo "  sso_account_id = YOUR_12_DIGIT_ACCOUNT_ID"
    echo "  sso_role_name  = AdministratorAccess"
    echo "  region         = us-east-1"
    echo "  output         = json"
    echo ""
    echo "WHERE TO FIND THESE VALUES:"
    echo "  sso_start_url  → AWS Console → IAM Identity Center → Settings → AWS access portal URL"
    echo "  sso_account_id → AWS Console → top-right account dropdown → 12-digit Account ID"
    echo "  sso_role_name  → IAM Identity Center → AWS accounts → [your account] → assigned permission sets"
    echo ""
    echo "After adding the block, re-run this script — it will open the browser for approval automatically."
    echo ""
    echo "To use a different profile: AWS_PROFILE=my-profile ./scripts/deploy/terraform.sh plan"
}

# Check credentials; if expired/missing, attempt SSO login then re-check — exits with setup instructions if still failing
if ! aws sts get-caller-identity --profile "$AWS_PROFILE" &>/dev/null; then
    echo "SSO session expired or not started — running: aws sso login --profile $AWS_PROFILE"
    if ! aws sso login --profile "$AWS_PROFILE"; then
        _sso_setup_instructions
        exit 1
    fi
    # Re-verify after login
    if ! aws sts get-caller-identity --profile "$AWS_PROFILE" &>/dev/null; then
        _sso_setup_instructions
        exit 1
    fi
fi

# Print the active AWS identity so you can confirm you're using the right account
echo "AWS identity: $(aws sts get-caller-identity --profile "$AWS_PROFILE" --query 'Arn' --output text)"

# Detect current public IP automatically — updates the security group ingress rule on every apply
CURRENT_IP="${SSH_INGRESS_CIDR:-$(curl -fsSL ifconfig.me 2>/dev/null)/32}"
if [ -z "$CURRENT_IP" ] || [ "$CURRENT_IP" = "/32" ]; then
    echo "ERROR: Could not detect your public IP. Set SSH_INGRESS_CIDR manually and retry."
    exit 1
fi
echo "Using ssh_ingress_cidr: $CURRENT_IP"

# Derive the EC2 public key from the private key configured for ec2-stock in ~/.ssh/config
# This is needed because Terraform registers the public key in AWS when creating the instance;
# the local .pem alone only works with already-running instances — it doesn't register anything in AWS.
SSH_KEY_PATH=$(ssh -G ec2-stock 2>/dev/null | awk '/^identityfile/ {print $2; exit}')
SSH_KEY_PATH="${SSH_KEY_PATH/#\~/$HOME}"  # expand leading ~ to $HOME so ssh-keygen can open the file
if [[ -z "$SSH_KEY_PATH" || ! -f "$SSH_KEY_PATH" ]]; then
    echo "ERROR: Could not find SSH private key for 'ec2-stock' in ~/.ssh/config"
    echo "  Make sure IdentityFile is set under 'Host ec2-stock' in ~/.ssh/config"
    exit 1
fi
export TF_VAR_ssh_public_key
TF_VAR_ssh_public_key=$(ssh-keygen -y -f "$SSH_KEY_PATH")  # extract public key from .pem — Terraform picks this up automatically via TF_VAR_
echo "Using SSH key: $SSH_KEY_PATH"

ACTION="${1:-plan}"

case "$ACTION" in
    init)
        # COST: FREE — downloads provider plugins to .terraform/ locally; makes no AWS API calls
        terraform -chdir="$TF_DIR" init
        ;;
    plan)
        # COST: FREE — read-only AWS API calls to show what apply would change; nothing is created or modified
        terraform -chdir="$TF_DIR" plan -var="ssh_ingress_cidr=$CURRENT_IP"
        ;;
    apply)
        # Guard: block apply only if an EC2 instance already exists in AWS but hasn't been imported into state
        # (when no instance exists in AWS yet it is safe to let apply create one)
        GUARD_INSTANCE_ID=$(aws ec2 describe-instances \
            --profile "$AWS_PROFILE" \
            --filters "Name=tag:Name,Values=data-pipeline-ec2" "Name=instance-state-name,Values=running,stopped" \
            --query "Reservations[0].Instances[0].InstanceId" \
            --output text 2>/dev/null || echo "")
        if [ -n "$GUARD_INSTANCE_ID" ] && [ "$GUARD_INSTANCE_ID" != "None" ]; then
            # Instance exists in AWS — ensure it's been imported before applying to avoid duplicates
            if ! terraform -chdir="$TF_DIR" state show aws_instance.pipeline &>/dev/null; then
                echo "ERROR: aws_instance.pipeline ($GUARD_INSTANCE_ID) exists in AWS but is not in Terraform state."
                echo "  apply would create a brand new blank EC2 instance instead of managing your existing one."
                echo "  Run import first: ./scripts/deploy/terraform.sh import"
                exit 1
            fi
        fi

        # Pre-flight plan: detect instance replacement before applying — auto-snapshot root EBS volume if replacement found
        PLAN_TMPFILE="$(mktemp)"
        trap 'rm -f "$PLAN_TMPFILE"' EXIT  # always clean up temp file on exit
        echo "Running pre-apply plan to check for instance replacement..."
        terraform -chdir="$TF_DIR" plan -no-color -var="ssh_ingress_cidr=$CURRENT_IP" | tee "$PLAN_TMPFILE"

        # If plan shows the instance will be replaced, snapshot the root EBS volume before proceeding
        if grep -q "aws_instance.pipeline must be replaced" "$PLAN_TMPFILE"; then
            echo ""
            echo "WARNING: Plan will REPLACE the EC2 instance (destroy + recreate)."
            echo "  Auto-snapshotting root EBS volume before proceeding to protect your data..."
            # Look up instance ID by the Name tag
            INSTANCE_ID=$(aws ec2 describe-instances \
                --profile "$AWS_PROFILE" \
                --filters "Name=tag:Name,Values=data-pipeline-ec2" "Name=instance-state-name,Values=running,stopped" \
                --query "Reservations[0].Instances[0].InstanceId" \
                --output text 2>/dev/null || true)
            if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
                # Grab the first (root) block device volume ID
                ROOT_VOL_ID=$(aws ec2 describe-instances \
                    --profile "$AWS_PROFILE" \
                    --instance-ids "$INSTANCE_ID" \
                    --query "Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId" \
                    --output text 2>/dev/null || true)
                if [ -n "$ROOT_VOL_ID" ] && [ "$ROOT_VOL_ID" != "None" ]; then
                    # Create EBS snapshot tagged for easy identification in the AWS Console
                    SNAPSHOT_ID=$(aws ec2 create-snapshot \
                        --profile "$AWS_PROFILE" \
                        --volume-id "$ROOT_VOL_ID" \
                        --description "Auto-snapshot before Terraform instance replacement $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
                        --tag-specifications "ResourceType=snapshot,Tags=[{Key=Name,Value=pipeline-pre-replace},{Key=Project,Value=data-pipeline}]" \
                        --query "SnapshotId" --output text 2>/dev/null || true)
                    if [ -n "$SNAPSHOT_ID" ] && [ "$SNAPSHOT_ID" != "None" ]; then
                        echo "  Snapshot created: $SNAPSHOT_ID (from volume $ROOT_VOL_ID) — completes in background"
                        echo "  Monitor: aws ec2 describe-snapshots --profile $AWS_PROFILE --snapshot-ids $SNAPSHOT_ID --query 'Snapshots[0].State'"
                    else
                        echo "  WARNING: Snapshot creation failed — verify in AWS Console before proceeding."
                    fi
                else
                    echo "  WARNING: Root volume ID not found — skipping snapshot."
                fi
            else
                echo "  WARNING: Instance not found by tag data-pipeline-ec2 — skipping snapshot."
            fi
            echo ""
        fi

        # COST: COSTS MONEY — provisions/modifies AWS resources (EC2, Elastic IP, security groups, IAM roles)
        terraform -chdir="$TF_DIR" apply -var="ssh_ingress_cidr=$CURRENT_IP"
        # Clear stale SSH known_hosts entry for the EIP in case the instance was recreated with a new host key
        EIP=$(terraform -chdir="$TF_DIR" output -raw elastic_ip 2>/dev/null || true)
        if [ -n "$EIP" ]; then
            ssh-keygen -R "$EIP" &>/dev/null || true
            echo "Cleared SSH known_hosts entry for $EIP — reconnect will re-verify the host key."
        fi
        ;;
    import)
        # Import pre-existing AWS resources into Terraform state — safe to re-run; skips already-tracked resources.
        # Must be run before apply when resources exist in AWS but are absent from state.

        # Skip import if resource is already tracked — makes this action fully idempotent
        _import_if_missing() {
            local addr="$1" id="$2"
            if terraform -chdir="$TF_DIR" state show "$addr" &>/dev/null; then
                echo "  already in state: $addr — skipping"
            else
                # Pass ssh_ingress_cidr so Terraform resolves all vars without interactive prompting
            terraform -chdir="$TF_DIR" import -var="ssh_ingress_cidr=$CURRENT_IP" "$addr" "$id"
            fi
        }

        echo "Importing pre-existing resources into Terraform state..."
        _import_if_missing aws_iam_role.ec2_ecr_role              ec2-ecr-role
        _import_if_missing aws_ecr_repository.flask_app           my-flask-app
        # Console auto-creates a profile with the same name as the role (ec2-ecr-role) — import that if it exists
        _PROFILE=$(aws iam get-instance-profile \
            --instance-profile-name ec2-ecr-role \
            --profile "$AWS_PROFILE" \
            --query "InstanceProfile.InstanceProfileName" \
            --output text 2>/dev/null || echo "")
        if [ -n "$_PROFILE" ] && [ "$_PROFILE" != "None" ]; then
            _import_if_missing aws_iam_instance_profile.ec2_ecr_profile ec2-ecr-role
        else
            echo "  aws_iam_instance_profile.ec2_ecr_profile not found in AWS — apply will create it"
        fi

        # IAM policy attachment ID is the composite "role-name/policy-arn" string
        # Policy may not exist if the role was set up manually without this attachment — apply creates it
        _POLICY=$(aws iam list-attached-role-policies \
            --role-name ec2-ecr-role \
            --profile "$AWS_PROFILE" \
            --query "AttachedPolicies[?PolicyArn=='arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser'].PolicyArn" \
            --output text 2>/dev/null || echo "")
        if [ -n "$_POLICY" ] && [ "$_POLICY" != "None" ]; then
            _import_if_missing aws_iam_role_policy_attachment.ecr_power_user \
                "ec2-ecr-role/arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPowerUser"
        else
            echo "  aws_iam_role_policy_attachment.ecr_power_user not found in AWS — apply will create it"
        fi

        # Look up the existing EC2 instance by Name tag — prevents Terraform from creating a duplicate
        EXISTING_INSTANCE_ID=$(aws ec2 describe-instances \
            --profile "$AWS_PROFILE" \
            --filters "Name=tag:Name,Values=data-pipeline-ec2" \
                      "Name=instance-state-name,Values=running,stopped" \
            --query "Reservations[].Instances[].InstanceId" \
            --output text)
        if [ -n "$EXISTING_INSTANCE_ID" ]; then
            echo "Found existing EC2 instance: $EXISTING_INSTANCE_ID"
            _import_if_missing aws_instance.pipeline "$EXISTING_INSTANCE_ID"
        else
            echo "No existing EC2 instance found — apply will create one."
        fi

        # Import EIP association if the EIP is already bound to an instance — otherwise apply will create it
        EIP_ASSOC_ID=$(aws ec2 describe-addresses \
            --profile "$AWS_PROFILE" \
            --filters "Name=tag:Name,Values=pipeline-eip" \
            --query "Addresses[0].AssociationId" \
            --output text 2>/dev/null || true)
        if [ -n "$EIP_ASSOC_ID" ] && [ "$EIP_ASSOC_ID" != "None" ]; then
            echo "Found EIP association: $EIP_ASSOC_ID"
            _import_if_missing aws_eip_association.pipeline_eip_assoc "$EIP_ASSOC_ID"
        else
            echo "EIP is not currently associated — apply will create the association."
        fi

        # Import key pair if it already exists in AWS under the configured name
        _KP_NAME="${TF_VAR_key_pair_name:-kafkaProjectKeyPair_4-29-2025}"  # fall back to default if not overridden via env
        _KEY_PAIR=$(aws ec2 describe-key-pairs \
            --profile "$AWS_PROFILE" \
            --key-names "$_KP_NAME" \
            --query "KeyPairs[0].KeyName" \
            --output text 2>/dev/null || echo "")
        if [ -n "$_KEY_PAIR" ] && [ "$_KEY_PAIR" != "None" ]; then
            echo "Found existing key pair: $_KP_NAME"
            _import_if_missing aws_key_pair.pipeline "$_KP_NAME"
        else
            echo "  aws_key_pair.pipeline not found in AWS — apply will register the public key from $SSH_KEY_PATH"
        fi

        echo ""
        echo "Import complete. Run plan to verify."
        echo "  Expected remaining '+ create' items (handled by apply, not import issues):"
        echo "    aws_ecr_lifecycle_policy.flask_app_lifecycle  — new resource, will be created"
        echo "    aws_instance.pipeline must be replaced        — encrypted EBS change, auto-snapshot will fire"
        ;;
    destroy)
        # COST: STOPS CHARGES — terminates all managed resources; the Elastic IP is permanently released
        # Require explicit confirmation — look up the current EIP so the warning is accurate
        CURRENT_EIP=$(terraform -chdir="$TF_DIR" output -raw elastic_ip 2>/dev/null || echo "unknown")
        echo "WARNING: This will destroy ALL managed infrastructure including the Elastic IP."
        echo "The Elastic IP ($CURRENT_EIP) will be permanently released. Type 'yes' to confirm."
        read -r CONFIRM
        if [ "$CONFIRM" = "yes" ]; then
            terraform -chdir="$TF_DIR" destroy -var="ssh_ingress_cidr=$CURRENT_IP"
        else
            echo "Aborted."
            exit 1
        fi
        ;;
    *)
        echo "ERROR: Unknown action '$ACTION'. Valid actions: init, plan, apply, import, destroy"
        exit 1
        ;;
esac
