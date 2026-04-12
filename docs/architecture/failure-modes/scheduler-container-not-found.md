# Incident: "container not found (scheduler)" in Steps 7b and 7c

**Date:** 2026-04-11
**Severity:** Deploy failure — ml-venv not built, MLflow experiment not reset, anomaly_detector.py unable to run

---

## What Happened

Running `./scripts/deploy.sh` failed at Step 7b (ml-venv setup) and Step 7c (MLflow experiment reset) with:

```
error: Internal error occurred: unable to upgrade connection: container not found ("scheduler")
```

Both steps run `kubectl exec airflow-scheduler-0` to do work inside the scheduler container.
The same error appeared in the deploy summary as:
- `WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved.`
- `DEPLOY FAILED (exit code: 1)`

---

## Root Cause

Step 7 (`step_restart_airflow_pods`) deletes and re-creates the scheduler pod, then in Phase B.5
polls until `kubectl exec airflow-scheduler-0 -- /bin/true` succeeds. That confirms exec-readiness —
but only for that one SSH session, at that one moment in time.

Steps 7b and 7c each open their own fresh SSH sessions. Between Phase B.5 finishing and those
sessions starting, the scheduler container can become briefly unreachable again. Common causes:

- K3S container runtime needing extra settling time after the pod turns Ready
- A liveness probe firing and briefly interrupting exec connections
- A transient API server hiccup between SSH connections

Because `step_setup_ml_venv` and `step_fix_mlflow_experiment` had no readiness check of their own,
they attempted `kubectl exec` immediately and hit the race condition.

---

## Fix

Extracted the Phase B.5 polling loop into a reusable helper `_wait_scheduler_exec()` in
`scripts/deploy/airflow_pods.sh`. The helper polls up to 30 times (60 seconds) until
`kubectl exec ... /bin/true` succeeds, and exits with an error if it never does.

Added a call to `_wait_scheduler_exec()` at the start of:
- `step_setup_ml_venv` (Step 7b) — before building the ml-venv
- `step_fix_mlflow_experiment` (Step 7c) — before running the Python MLflow fix in the scheduler

Phase B.5 in `step_restart_airflow_pods` now also calls `_wait_scheduler_exec()` instead of
repeating the loop inline.

Each step that execs into the scheduler now gets its own independent readiness confirmation,
eliminating the race condition between SSH sessions.

---

## Files Changed

- `scripts/deploy/airflow_pods.sh` — added `_wait_scheduler_exec()` helper; replaced Phase B.5 inline loop; added call in `step_setup_ml_venv`
- `scripts/deploy/mlflow.sh` — added `_wait_scheduler_exec()` call in `step_fix_mlflow_experiment`

---

## How to Verify the Fix Worked

After running `./scripts/deploy.sh`, check for:
1. `Scheduler container exec-ready (attempt N)` printed before Step 7b and 7c
2. `ml-venv OK — all packages importable` in Step 7b output
3. `MLFLOW_TRACKING_URI set.` in Step 7c output
4. Deploy exits with code 0
