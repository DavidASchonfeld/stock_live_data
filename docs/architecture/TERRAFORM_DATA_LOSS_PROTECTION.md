# Terraform Data Loss Protection

## The Problem

The EC2 instance's root EBS volume (100 GB) holds everything: K3s images, Airflow state, MLflow
artifacts, MariaDB data, and all installed packages. If Terraform ever destroys and recreates the
instance (a "replacement"), AWS deletes that volume by default because `delete_on_termination`
defaults to `true`.

Certain `main.tf` changes force a replacement instead of an in-place update:

| Change | Effect |
|---|---|
| `volume_size` changed | Instance destroyed + recreated — volume survives (`delete_on_termination = false`) |
| `volume_type` changed | Instance destroyed + recreated — volume survives |
| `encrypted` changed | Instance destroyed + recreated — volume survives |
| `delete_on_termination` changed | Instance destroyed + recreated — volume survives |
| `key_name` changed | Instance destroyed + recreated — volume survives |
| `instance_type` changed | Stopped, resized, restarted — data safe |
| `ami` changed | No effect (`ignore_changes = [ami]` is set) |

**Note:** `encrypted = true` and `delete_on_termination = false` are both set in `main.tf` and
differ from the original live instance. The **first apply after import** will trigger a replacement
— this is intentional. The auto-snapshot in `terraform.sh` fires before replacement, and the
volume is preserved by `delete_on_termination = false`. No data loss occurs.

## How It Was Identified

A review of `terraform/main.tf` found that `root_block_device` had no `delete_on_termination`
setting, so AWS applied the default (`true`). The doc `TERRAFORM_SSO_SETUP.md` called this out
explicitly after the Terraform setup was completed and the risk was visible in the config.

## The Fix — Two Layers

### Layer 1: `delete_on_termination = false` in `main.tf`

```hcl
root_block_device {
  volume_type           = "gp3"
  volume_size           = 100
  encrypted             = true   # free; zero performance impact — encrypts all data at rest
  delete_on_termination = false  # preserve root EBS volume on instance destruction — prevents data loss
}
```

**What it does:** If the instance is terminated (by Terraform or manually), AWS detaches the EBS
volume instead of deleting it. The volume stays in your account in `available` state and can be
reattached to the new instance or used to restore data.

**Why this is the primary fix:** It changes the default behavior at the AWS level — no code path
can bypass it. Even if Terraform replaces the instance, the volume survives.

### Layer 2: Auto-snapshot before replacement in `terraform.sh`

The `apply` case in `scripts/deploy/terraform.sh` now runs a pre-flight `terraform plan` before
every apply. If the plan output contains `aws_instance.pipeline must be replaced`, the script:

1. Looks up the instance ID by the `Name=data-pipeline-ec2` tag
2. Finds the root EBS volume ID
3. Creates an EBS snapshot tagged `pipeline-pre-replace`
4. Prints the snapshot ID and a command to monitor its progress
5. Then proceeds with `terraform apply`

**Why this is the secondary fix:** Snapshots give you a point-in-time backup that can be used to
create a new root volume and mount it on a replacement instance. Even with `delete_on_termination =
false`, having a snapshot is faster to restore from than reattaching a detached volume and
bootstrapping from scratch.

## How to Recover if the Instance Is Replaced

If a replacement happens (volume detached, not deleted):

1. **Find the old volume** — AWS Console → EC2 → Volumes → filter by `available` state
2. **Create a new instance** (or let Terraform do it via apply)
3. **Attach the old volume** to the new instance as a secondary volume (`/dev/sdb`)
4. **Mount and copy** the data you need: `sudo mount /dev/sdb /mnt/old && cp -a /mnt/old/... /target/`

If you have a snapshot:

1. AWS Console → EC2 → Snapshots → find `pipeline-pre-replace`
2. Actions → Create Volume → attach to the new instance

## Does This Cost Extra?

**Short answer: Layer 1 costs nothing extra under normal conditions. Layer 2 costs a few cents per
snapshot, and only when a replacement is actually triggered.**

### Layer 1 — `delete_on_termination = false`

No additional cost by itself. The EBS volume already costs ~$8/month whether it is attached to a
running instance or sitting detached. Setting this flag just changes what happens when the instance
is terminated — it does not provision anything new.

**The one caveat:** if the instance IS replaced, you now have a detached "orphan" volume floating in
your account. AWS keeps charging ~$8/month for it until you manually delete it. Once you have
recovered your data (or confirmed you don't need it), go to AWS Console → EC2 → Volumes, find the
`available` volume, and delete it to stop the charge.

### Layer 2 — Auto-snapshot

EBS snapshots are charged at **~$0.05 per GB per month** for the data actually stored, not the full
volume size. A 100 GB volume that is 30 GB full costs about **$1.50/month** to keep as a snapshot.

A few things that keep the cost low:

- **Snapshots only run when replacement is detected.** A normal `terraform apply` that just updates
  the security group IP (the most common operation) runs the plan check, sees no replacement, and
  creates no snapshot. You are not charged. You could run `apply` 100 times updating your IP and
  accumulate zero snapshots. A snapshot only fires on the rare occasion Terraform needs to destroy
  and recreate the EC2 instance itself (e.g. changing `volume_size` or `key_name` — see the table
  above).
- **AWS snapshots are incremental after the first.** If you ever trigger a second replacement, only
  the blocks that changed since the last snapshot are stored — so the second snapshot costs
  significantly less than the first.
- **Snapshots do not auto-delete.** The script creates the snapshot but does not clean it up. Once
  you have verified your new instance is stable after a replacement, delete old snapshots manually
  from AWS Console → EC2 → Snapshots to avoid ongoing storage charges.

### Summary

| Scenario | Extra cost |
|---|---|
| Normal deploy (no replacement) | $0 — snapshot never runs |
| Instance replaced, volume detached | ~$8/month for the orphan volume until you delete it |
| Snapshot created on replacement | ~$0.05/GB-month for used blocks (~$1–2/month for this volume) |
| After recovery, you delete both | $0 — no lingering charges |

## What Was NOT Changed

`prevent_destroy = true` was deliberately left out. It would block `terraform destroy` (the teardown
command used to shut down the project) and would also block legitimate replacements — requiring a
manual `main.tf` edit before any infrastructure change could proceed. The two-layer approach above
(`delete_on_termination = false` + auto-snapshot) provides strong protection without that friction.

## How deploy.sh Fits Into Recovery

**The short answer: yes — if Terraform replaces the instance, you just re-run `./scripts/deploy.sh`
and the pipeline comes back up. The snapshot is extra insurance, not a required step.**

`./scripts/deploy.sh` (and the modules under `scripts/deploy/`) rebuilds the entire pipeline from
scratch on whatever EC2 instance Terraform gives you — K3S, Airflow, Kafka, MLflow, the Flask
dashboard, all DAGs, secrets, and Helm charts. So the recovery workflow after a replacement is just:

```
./scripts/deploy.sh --provision
```

That runs `terraform apply` first (which creates the new instance), then does the full deploy. Wait
for pods to come up and the pipeline is live again.

A few nuances worth knowing:

1. **Your actual pipeline data is safe regardless.** The financial and weather data
   (RAW → STAGING → MARTS → FCT_ANOMALIES) all lives in Snowflake, not on the EC2 instance.
   Instance replacement doesn't touch it.

2. **The EBS snapshot is insurance for things that DO live on EC2.** That includes the Airflow
   metadata DB (DAG run history, task logs), MLflow experiment runs and model artifacts, and any
   Kafka log segments not yet consumed to Snowflake. None of this is critical to re-running the
   pipeline, but it's useful if you want to preserve history.

3. **You cannot auto-reattach the old EBS to the new instance.** When Terraform replaces an
   instance, the new instance gets a fresh root volume. The old volume is preserved (not deleted)
   but sits detached. You'd have to manually mount it as a secondary volume if you wanted something
   off it. The snapshot is the cleaner recovery path if you actually need data from it (see
   "How to Recover" above).

4. **Normal workflow after replacement:** run `./scripts/deploy.sh --provision`, wait for pods to
   come up, done. Check the snapshot only if you realize you need something from the old instance
   (e.g. an MLflow run you want to preserve).
