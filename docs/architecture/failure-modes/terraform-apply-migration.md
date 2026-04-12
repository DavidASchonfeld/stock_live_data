# Terraform Apply — EC2 Migration Runbook

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

## Context

This runbook applies when you are running `terraform apply` for the first time against a production
environment that has an existing manually-created EC2 instance. After apply you will have **two**
instances running simultaneously. This document covers what happens, how to verify, and how to safely
decommission the old instance to avoid double-billing.

---

## What Happens During `apply`

| Step | What Terraform does | Effect on old instance |
|------|--------------------|-----------------------|
| Creates `aws_instance.pipeline` | Provisions a new EC2 (t3.large, Ubuntu 24.04, 100 GB gp3 encrypted) | None |
| Creates `aws_eip_association.pipeline_eip_assoc` | Attaches the existing EIP to the **new** instance | **Old instance loses the EIP** — its public IP disappears |
| Creates `aws_ecr_lifecycle_policy.flask_app_lifecycle` | Adds ECR image expiry policy for untagged images | None |
| Updates `aws_ecr_repository.flask_app` | Adds `Project` tag + enables scan-on-push | None |
| Updates `aws_iam_role.ec2_ecr_role` | Adds `Project` tag | None |

**No destroys.** The old instance keeps running — but it loses its EIP the moment the association
is moved.

---

## Bootstrapping the New Instance — Common Questions

**Q: What does "bootstrap the new instance" mean? Do I need to do anything special?**

No, just run your deploy script:
```bash
./scripts/deploy.sh
```
That's it. The deploy script handles everything — installing K3S, Airflow, Kafka, MLflow, and Flask
from scratch on the new instance. There is no separate setup step.

---

**Q: After `terraform apply`, will the deploy script automatically target the new instance, or do I need to update something?**

It automatically targets the new instance. No config changes needed.

Here's why: the deploy script connects using the SSH alias `ec2-stock`, which is defined in
`~/.ssh/config` and points to the EIP's IP address. The EIP itself doesn't change — it's the same
IP, just moved from the old instance to the new one. So `ec2-stock` already resolves to the right
place the moment `apply` finishes.

---

**Q: How long do I have to wait after `terraform apply` before running the deploy script?**

About 2 minutes. The new instance starts as a completely blank Ubuntu 24.04 server — nothing is
pre-installed. AWS needs a minute to boot it up, and then another minute for SSH to come online.

Before running `deploy.sh`, test that SSH is actually ready:
```bash
ssh ec2-stock echo ok
```
When that prints `ok` without any error, you're good to go. Do not run `deploy.sh` before this
succeeds — it will fail if the instance isn't SSH-ready yet.

---

**Critical: The EIP moves the instant `apply` completes.**

As soon as `terraform apply` finishes, the EIP (your static public IP) is detached from the old
instance and attached to the new one. The old instance loses its public IP immediately — this is
expected, not a bug.

What this means in practice: do not terminate the old instance yet. It is still running (just
without a public IP) and you may need it as a fallback. Only terminate it after `deploy.sh`
finishes successfully and all pods show `Running` on the new instance.

---

## How Long to Wait Before Running the Deploy Script

The new instance is a blank Ubuntu 24.04 (all provisioning is done via `deploy.sh` over SSH).
After `apply` completes:

- **~1 min** — EC2 transitions from `pending` → `running` in AWS
- **~2 min** — SSH daemon starts and accepts connections

**Test SSH readiness before running `deploy.sh`:**
```bash
ssh ec2-stock echo ok
```
When this prints `ok` without error, the instance is ready. Do not run `deploy.sh` before this succeeds.

---

## Post-Apply Verification Checklist

Run these checks after `apply` and before decommissioning the old instance.

### 1. Terraform outputs look correct
```bash
./scripts/deploy/terraform.sh plan   # should show: No changes
```

### 2. New instance is reachable
```bash
ssh ec2-stock echo ok
ssh ec2-stock curl -s http://169.254.169.254/latest/meta-data/instance-id
```
Confirm the instance ID matches the one printed in `terraform apply` output (the `instance_id` output).

### 3. IAM instance profile attached (ECR access will fail without this)
```bash
ssh ec2-stock curl -s http://169.254.169.254/latest/meta-data/iam/info | grep InstanceProfileArn
```
Expected: `"InstanceProfileArn": "arn:aws:iam::...:instance-profile/ec2-ecr-role"`

### 4. Deploy script runs clean
```bash
./scripts/deploy.sh
```
Watch for SSH errors or pod failures. First run provisions everything from scratch (K3S, Airflow,
Kafka, MLflow, Flask). This will take several minutes.

### 5. Services are healthy
```bash
ssh ec2-stock kubectl get pods -A
```
All pods should reach `Running` or `Completed`. No `CrashLoopBackOff` or `ImagePullBackOff`.

---

## Common Errors After Apply

### SSH connection refused / timeout
- **Cause:** Instance still booting. Wait and retry `ssh ec2-stock echo ok`.
- **Check:** AWS Console → EC2 → Instance state = `running`, Status checks = `2/2 passed`.

### ImagePullBackOff after deploy
- **Cause:** IAM instance profile not attached, or ECR token stale.
- **Check:** Run the IAM verification step above. Then re-run `./scripts/deploy.sh`.

### `terraform plan` still shows changes after apply
- **Cause:** AWS propagation lag or a resource drifted.
- **Check:** Wait 60 seconds and re-run plan. If changes persist, read the diff carefully before
  applying again.

### EIP not associated to new instance
- **Cause:** `aws_eip_association` creation failed mid-apply.
- **Check:** `aws ec2 describe-addresses --allocation-ids eipalloc-01a104daeec39e3e3` — verify
  `InstanceId` matches the new instance ID.
- **Fix:** Re-run `./scripts/deploy/terraform.sh apply` — Terraform is idempotent and will retry.

---

## Decommissioning the Old Instance

Only do this after the post-apply checklist is fully green.

### Step 1 — Identify old instance
In AWS Console → EC2, filter by `Name = data-pipeline-ec2`. You will see **two** instances.
The new one is the one whose instance ID matches `terraform output instance_id`. The other is the old one.

### Step 2 — Snapshot old EBS volume (optional but recommended)
```bash
# Get old instance's root volume ID from AWS Console → Volumes, or:
aws ec2 describe-instances --instance-ids <OLD_INSTANCE_ID> \
  --query "Reservations[0].Instances[0].BlockDeviceMappings[0].Ebs.VolumeId" --output text
# Then snapshot it:
aws ec2 create-snapshot --volume-id <VOL_ID> \
  --description "Final snapshot before decommission old manual EC2 $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

### Step 3 — Terminate old instance
AWS Console → EC2 → select old instance → Instance state → Terminate.

Or via CLI:
```bash
aws ec2 terminate-instances --instance-ids <OLD_INSTANCE_ID>
```

### Step 4 — Delete orphaned EBS volumes
After termination, check for volumes left in `available` state (not auto-deleted because
`delete_on_termination=false` was likely set on the old instance):

AWS Console → EC2 → Volumes → filter State = `available`.
Delete any volumes that belonged to the old instance.

### Step 5 — Verify EIP is not unassociated
The EIP should already be associated to the new instance (done by `apply`). Confirm:
```bash
aws ec2 describe-addresses --allocation-ids eipalloc-01a104daeec39e3e3 \
  --query "Addresses[0].{IP:PublicIp,InstanceId:InstanceId}"
```
An unassociated EIP costs ~$0.005/hr — if for any reason it's loose, associate it or release it.

---

## Billing Impact

| Resource | After apply (before cleanup) | After cleanup |
|----------|------------------------------|---------------|
| EC2 t3.large | 2× billing (~$0.0832/hr each) | 1× |
| EBS volumes | 2× (old vol + new 100 GB gp3) | 1× |
| EIP | Free (attached to new instance) | Free |

**Do not leave the old instance running more than a day** — it has no function once the EIP is gone
and the deploy script is targeting the new instance.

---

### EC-8: `apply` Blocked — "not in Terraform state" When No Instance Exists (Apr 11 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | `apply` exits with `ERROR: aws_instance.pipeline is not in Terraform state` even after `import` ran cleanly and reported "No existing EC2 instance found." |
| **Cause** | The guard checked only whether the resource was absent from state — it didn't distinguish between "instance missing from state because it was never imported" and "instance genuinely doesn't exist in AWS yet." The former is dangerous; the latter is the normal first-time-create path. |
| **Fix** | Guard now first queries AWS for an instance with `Name=data-pipeline-ec2`. It only blocks if an instance **exists in AWS** but is absent from state. If no instance exists in AWS, apply proceeds and creates one. Fixed in `scripts/deploy/terraform.sh`. |
| **Real incident?** | Yes — Apr 11 2026. First-time `apply` after migrating infra to Terraform. |

---

### EC-9: `apply` Failed — Key Pair Not Found in AWS (Apr 11 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | `apply` exits with `InvalidKeyPair.NotFound: The key pair 'kafkaProjectKeyPair_4-29-2025' does not exist` |
| **Cause** | The AWS EC2 Key Pairs registry entry was deleted from the AWS Console while the local `.pem` file remained intact. The local `.pem` is only used by your SSH client to authenticate to an *already-running* instance — it does not register anything in AWS. For Terraform to *create* a new instance, it must reference a key pair registered in AWS so AWS can inject the public key into `authorized_keys` at boot. |
| **Fix** | Added `aws_key_pair` resource to `terraform/main.tf`. `terraform.sh` now auto-extracts the public key from the `.pem` referenced in `~/.ssh/config` for `ec2-stock` using `ssh-keygen -y`, exports it as `TF_VAR_ssh_public_key`, and the `import` action imports the key pair into state if it already exists in AWS. No new credentials are created — the same `.pem` is reused. |
| **Real incident?** | Yes — Apr 11 2026. |

---

### EC-7: Terraform Apply — First-Time Production Migration (Apr 11 2026)

| Field | Detail |
|-------|--------|
| **Situation** | First `terraform apply` against a pre-existing manual EC2. Two instances exist simultaneously post-apply. |
| **Key risk** | Old instance loses EIP instantly on apply. Double-billing until old instance is terminated. |
| **Resolution** | Follow the decommission steps above. Verify deploy script health before terminating old instance. |
| **Real incident?** | Planned migration — Apr 11 2026. One-time for this project. |
| **Status** | **Apply succeeded.** Resources created: `aws_key_pair.pipeline` (kafkaProjectKeyPair_4-29-2025), `aws_instance.pipeline` (i-08f429ea763e04c82, t3.large, Ubuntu 24.04, 100 GB gp3 encrypted), `aws_eip_association.pipeline_eip_assoc` (EIP 100.30.3.22 associated). Next steps: wait ~2 min, `ssh ec2-stock echo ok`, then `./scripts/deploy.sh`, then verify pods on new instance, then terminate old instance. |
