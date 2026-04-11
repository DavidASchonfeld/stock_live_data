# Incident: Helm Post-Upgrade Hook Timeout (Step 2d)

**Date:** 2026-04-10
**Severity:** Low (deploy succeeded despite error; upgrade was applied)
**Status:** Resolved

---

## What Happened

Every run of `./scripts/deploy.sh` produced this error in Step 2d:

```
Error: UPGRADE FAILED: post-upgrade hooks failed: 1 error occurred:
    * timed out waiting for the condition
```

The deploy continued anyway (the upgrade was actually applied), but the output was noisy and the `|| echo` workaround was masking a real failure signal.

---

## Root Cause

The Apache Airflow Helm chart 1.20.0 ships with `migrateDatabaseJob.useHelmHooks: true` by default. This creates the `airflow-run-airflow-migrations` Job as a **Helm post-upgrade hook**, meaning `helm upgrade` blocks and waits for the job to finish before returning.

On this single-node K3S cluster, the migration job runs while the old pods are still terminating. The scheduler/webserver pods hold CPU and memory during their graceful shutdown, leaving the migration job pod in a resource-contended state. The job takes longer than the `--timeout 10m` limit Helm was given, so Helm reports the upgrade as failed — even though the job eventually completed successfully.

The previous workaround added `--atomic=false` (prevent auto-rollback on hook failure) and `|| echo` (suppress the non-zero exit code so `set -e` didn't abort the script). These masked the symptom but didn't fix the cause.

---

## How It Was Identified

The error appeared on every `./scripts/deploy.sh` run, immediately visible in Step 2d output. The `|| echo "Note: Helm hook timed out..."` message confirmed it was a recurring known issue that had been suppressed rather than fixed. Investigation into the Helm chart source confirmed that `migrateDatabaseJob.useHelmHooks` is a first-class supported option for exactly this scenario.

---

## Fix

In `airflow/helm/values.yaml`, added:

```yaml
migrateDatabaseJob:
  useHelmHooks: false
```

In `scripts/deploy.sh` Step 2d:
- Removed `--atomic=false` (was only needed because hook failure would otherwise trigger a rollback)
- Removed the `|| echo "Note: Helm hook timed out..."` suppressor
- Updated comments to reflect the new behavior

---

## Why This Fix Works

With `useHelmHooks: false`, the migration job is created as a regular Kubernetes Job (no `helm.sh/hook` annotations). `helm upgrade` no longer waits for it and returns immediately with success.

The migration still runs correctly. Each Airflow pod has a `waitForMigrations` init container (built into the chart) that runs `airflow db check-migrations` in a retry loop and holds the pod in `Init` state until the migration job marks the schema as current. Only then does the main container start. This preserves the correct startup ordering — pods never start before the DB schema is ready — without tying it to Helm's operation timeout.

Result: `helm upgrade` completes cleanly, migration runs asynchronously, pods start once migration is done, Step 7 `kubectl wait` succeeds as before.
