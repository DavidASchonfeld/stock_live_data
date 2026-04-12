# Terraform Operations

Day-to-day commands, workflows, and cost reference for the pipeline infrastructure.

---

## The Four Commands

| Command | What it does | Touches AWS? | Costs money? |
|---|---|---|---|
| `init` | Downloads the AWS provider plugin locally | No | Free |
| `plan` | Shows what `apply` would change — never modifies anything | Read-only | Free |
| `apply` | Creates or modifies AWS resources to match `main.tf` | Yes | Yes (if resources change) |
| `destroy` | Terminates all managed resources | Yes | Stops ongoing charges |

All commands are run via the wrapper script:

```bash
./scripts/deploy/terraform.sh [init|plan|apply|destroy|import]
```

---

## Most Common Operation: Update the Security Group IP

SSH access is locked to one IP at a time. When you change networks (different location, VPN, etc.),
run:

```bash
./scripts/deploy/terraform.sh apply
```

The script auto-detects your current public IP via `curl ifconfig.me` and updates the security group
rule. This takes about 10 seconds and costs nothing (security group rule changes are free).

Or combine with a full deploy:

```bash
./scripts/deploy.sh --provision
```

The `--provision` flag runs `terraform apply` first (Phase 0), then continues with the normal deploy.

---

## deploy.sh Flags and Terraform

| Flag | What it does | Runs Terraform? |
|---|---|---|
| _(none)_ | Full deploy: Docker build, Kafka, MLflow, Flask, Helm, pod restarts | No |
| `--dags-only` | Fast path: rsync DAGs + restart Airflow pods only (~5–7 min) | No |
| `--provision` | Runs `terraform apply` first, then full deploy | Yes |

---

## Previewing Changes Without Applying

```bash
./scripts/deploy/terraform.sh plan
```

Safe to run at any time. Shows exactly what `apply` would create, change, or destroy — nothing is
modified. Use this to confirm your intent before running `apply`.

---

## Disaster Recovery: Rebuilding from Scratch

If the instance is terminated and needs to be rebuilt:

```bash
# 1. Provision the AWS resources (creates instance, EIP, security group, ECR)
./scripts/deploy/terraform.sh init    # first time only
./scripts/deploy/terraform.sh apply

# 2. Check the new EIP in the outputs and update ~/.ssh/config if it changed
terraform -chdir=terraform output elastic_ip

# 3. Install all software on the fresh instance
./scripts/bootstrap_ec2.sh

# 4. Deploy the application stack
./scripts/deploy.sh
```

---

## SSH After an Instance Replacement

Certain `main.tf` changes force Terraform to destroy and recreate the instance (see
`TERRAFORM_DATA_PROTECTION.md` for the full list). When the instance is recreated, it gets a new
SSH host key. Your SSH client will refuse to connect and print:

```
WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!
```

This is SSH protecting you — not an error. `terraform.sh apply` automatically clears the stale entry
after every apply:

```bash
ssh-keygen -R <your-elastic-ip>
```

The next SSH connection will prompt you to confirm the new fingerprint once. That is normal.

---

## Destroying Everything

```bash
./scripts/deploy/terraform.sh destroy
```

Requires typing `yes` explicitly. The Elastic IP will be **permanently released** — if you rebuild
later you will get a different IP and will need to update `~/.ssh/config`.

Take a final EBS snapshot before destroying if you want to preserve your data:
AWS Console → EC2 → Volumes → select root volume → Actions → Create Snapshot.

**Never add `--destroy` to `deploy.sh`.** The destroy command lives in `terraform.sh` deliberately,
behind an explicit confirmation prompt, to keep a clear boundary between software deploys and
infrastructure teardown.

---

## Checking for Orphaned Resources

If you suspect a second instance was accidentally created (e.g., from running `apply` before
completing `import`), check with:

```bash
aws ec2 describe-instances \
  --filters "Name=instance-state-name,Values=running" \
  --query "Reservations[].Instances[].[InstanceId,Tags[?Key=='Name'].Value|[0]]" \
  --output table --profile terraform-dev
```

You should see exactly one instance named `data-pipeline-ec2`. If you see two, terminate the blank
one from the AWS Console.

---

## Cost Reference

| Resource | Running cost | Stopped cost | Notes |
|---|---|---|---|
| EC2 t3.large | ~$0.083/hr (~$60/mo) | ~$0/hr | Compute charges only when running |
| EBS 100 GB gp3 | ~$8/mo | ~$8/mo | Storage charges regardless of instance state |
| Elastic IP | Free | ~$0.005/hr (~$3.60/mo) | Free only when associated with a running instance |
| ECR storage | ~$0.10/GB/mo | — | Kept near $0 by the lifecycle policy (untagged images deleted after 1 day) |
| EBS snapshot (if triggered) | ~$0.05/GB-month for used blocks | — | Only created when `apply` detects instance replacement — not on normal deploys |
