# Helm Upgrade Failed — Orphaned Migration Job Missing Ownership Metadata

**Date:** 2026-04-10
**Severity:** High — `helm upgrade` aborted entirely; no Airflow pods updated

---

## What Happened

`./scripts/deploy.sh` Step 2d failed with:

```
Error: UPGRADE FAILED: Unable to continue with update: Job "airflow-run-airflow-migrations"
in namespace "airflow-my-namespace" exists and cannot be imported into the current release:
invalid ownership metadata;
label validation error: missing key "app.kubernetes.io/managed-by": must be set to "Helm";
annotation validation error: missing key "meta.helm.sh/release-name": must be set to "airflow";
annotation validation error: missing key "meta.helm.sh/release-namespace": must be set to "airflow-my-namespace"
```

The Helm upgrade was completely blocked — no Airflow pods were updated.

---

## How It Was Encountered and Identified

The error message directly identified `Job/airflow-run-airflow-migrations` as the blocking resource. Helm 3 requires every resource it manages to carry ownership labels and annotations (`app.kubernetes.io/managed-by`, `meta.helm.sh/release-name`, `meta.helm.sh/release-namespace`). Without these, Helm refuses to adopt the resource during an upgrade to prevent overwriting resources it doesn't own.

Reviewing `values.yaml`:
```yaml
# migrateDatabaseJob.useHelmHooks: false
```

With `useHelmHooks: false`, Helm manages the migration Job as a regular tracked resource (not a post-upgrade hook). However, the Job already present in the cluster was created under the old `useHelmHooks: true` model, where it was a hook — Helm hooks are not tracked with the standard ownership labels. This left a Job in the cluster that Helm could neither own nor skip.

---

## Root Cause

`migrateDatabaseJob.useHelmHooks` was changed from `true` to `false` in `values.yaml`. The previous Helm install created `Job/airflow-run-airflow-migrations` as a post-upgrade hook (no ownership metadata). When `useHelmHooks: false` is active, Helm tries to adopt the Job as a regular managed resource, but the missing ownership metadata fails validation and aborts the upgrade.

---

## Fix

Added **Step 2c3** to `scripts/deploy.sh` immediately before Step 2d (helm upgrade):

```bash
echo "=== Step 2c3: Deleting stale Airflow migration Job ==="
ssh "$EC2_HOST" "kubectl delete job airflow-run-airflow-migrations -n airflow-my-namespace --ignore-not-found=true \
    && echo 'Migration Job cleared (idempotent).'"
```

This deletes the orphaned Job before Helm runs, so Helm creates it fresh with proper ownership metadata on the next upgrade. `--ignore-not-found` makes the step idempotent — safe to run on every deploy whether the Job exists or not.

---

## Why This Fix

- The migration Job had already completed successfully (Airflow was running). Deleting it causes no data loss.
- Helm will recreate the Job on the next upgrade with correct ownership metadata.
- `--ignore-not-found` ensures idempotency — the step does nothing on deploys where the Job was already cleaned up.
- This is the standard fix for Helm's "cannot be imported" error; patching the Job in-place with the required labels is an alternative but more fragile (requires knowing the exact label/annotation keys) and unnecessary since the Job is transient by design.
