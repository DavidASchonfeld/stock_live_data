# Incident: kubectl exec timed out setting KAFKA_BOOTSTRAP_SERVERS

**Date:** 2026-04-10
**Severity:** Deploy blocker (non-data-loss)

## What Happened

`./scripts/deploy.sh` failed with:

```
error: timed out waiting for the condition on pods/airflow-scheduler-0
```

at the "Setting Airflow Variable: KAFKA_BOOTSTRAP_SERVERS" step, right after Kafka was deployed.

## How It Was Encountered

Observed at the end of Step 2b4 output, immediately after Kafka topics were confirmed ready. The deploy halted here and did not proceed to the MLflow or Helm steps.

## Root Cause

The KAFKA_BOOTSTRAP_SERVERS variable was being set immediately after Kafka deployed (deploy.sh lines 238–245). At that point, the airflow-scheduler-0 pod was in the middle of a recovery cycle triggered by the large image import in Step 2b2, which took ~270s. The `kubectl wait --for=condition=Ready --timeout=120s` timed out because 120s was insufficient for the scheduler to recover from that disruption.

## Fix

Removed the early variable-setting block entirely. Moved `airflow variables set KAFKA_BOOTSTRAP_SERVERS` into Step 7, where the scheduler pod is explicitly deleted, restarted, and confirmed Ready before any `kubectl exec` is run.

The new location is after `airflow dags list` in the Step 7 SSH chain — at that point, `kubectl wait --for=condition=Ready` has already succeeded, so the exec is guaranteed to work.

## Why This Fix Was Chosen

- Airflow Variables are stored in MariaDB, not in the pod — they survive pod restarts, so there is no need to set them before the restart cycle
- Step 7 already has a proven "wait until Ready" pattern used for dags list and ml-venv setup
- No new timeout logic needed; piggybacking on the existing wait is cleaner and more reliable
- Idempotent: setting the same value every deploy is harmless

## How the Fix Solved the Problem

By moving the `airflow variables set` call to after `kubectl wait --for=condition=Ready` in Step 7, the exec only runs when the scheduler is confirmed healthy. The race condition between image import and pod recovery is eliminated entirely.
