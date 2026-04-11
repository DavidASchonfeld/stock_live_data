# Incident: MLflow — Cannot Set Deleted Experiment as Active

**Date:** 2026-04-10
**Severity:** Blocking (anomaly detection task fails on every run after deploy)

---

## Error

```
mlflow.exceptions.MlflowException: Cannot set a deleted experiment 'anomaly_detection'
as the active experiment. You can restore the experiment, or permanently delete the
experiment to create a new one.
```

---

## How It Was Encountered

Surfaced during post-deploy verification — running `anomaly_detector.py` directly inside the scheduler pod:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

The script connected to MLflow successfully, then crashed at `set_experiment()` before any model training occurred.

---

## Root Cause

`deploy.sh` Step 7c was added to reset the `anomaly_detection` experiment's artifact root from the old local path (`/mlflow-data/artifacts`) to the new HTTP-proxied root (`mlflow-artifacts:/`). It did this by soft-deleting the experiment via `client.delete_experiment()`, with a comment stating "set_experiment() will recreate it on the next run."

That assumption was wrong. MLflow's `set_experiment()` **cannot** create a new experiment when a soft-deleted one with the same name already exists — it raises an exception instead. The soft-deleted experiment occupied the name, and `anomaly_detector.py:86` had no logic to handle that state.

---

## How It Was Identified

The exception message itself was explicit: "Cannot set a deleted experiment." Tracing backward from `anomaly_detector.py:86` (`mlflow.set_experiment("anomaly_detection")`) to `deploy.sh` Step 7c revealed the `client.delete_experiment()` call and the incorrect comment. The MLflow docs confirm that `set_experiment()` requires either a live experiment or no experiment at all — a soft-deleted one blocks creation.

---

## Fix

### 1. `airflow/dags/anomaly_detector.py` (lines 85–91) — defensive guard

Before calling `set_experiment()`, check the experiment's lifecycle stage and restore it if soft-deleted:

```python
# Restore soft-deleted experiment if present — set_experiment cannot reuse deleted experiments
_client = mlflow.tracking.MlflowClient()
_exp = _client.get_experiment_by_name("anomaly_detection")
if _exp is not None and _exp.lifecycle_stage == "deleted":
    _client.restore_experiment(_exp.experiment_id)
mlflow.set_experiment("anomaly_detection")
```

### 2. `scripts/deploy.sh` (Step 7c) — root cause fix

After soft-deleting the old experiment, immediately recreate it with the correct artifact root instead of leaving the name occupied by a deleted record:

```python
client.delete_experiment(exp.experiment_id)                       # soft-delete old experiment to clear stale artifact root
new_id = client.create_experiment('anomaly_detection', artifact_location='mlflow-artifacts:/')
print(f'Recreated anomaly_detection (id={new_id}) with artifact root mlflow-artifacts:/')
```

---

## Why These Fixes

- **`anomaly_detector.py` guard:** Makes the pipeline self-healing. If the experiment is ever left in a soft-deleted state (deploy failure, manual intervention, etc.) the script recovers on its own rather than crashing.
- **`deploy.sh` recreate:** Fixes the actual broken assumption. The old code deleted the experiment and expected `set_experiment()` to recreate it — MLflow doesn't work that way. Calling `create_experiment()` explicitly with `artifact_location='mlflow-artifacts:/'` is the correct way to reset the artifact root to the proxied URI.

---

## How the Fix Solved the Problem

- **`deploy.sh`** now leaves the `anomaly_detection` experiment in an active state with the correct artifact root after every deploy — `set_experiment()` finds a live experiment and proceeds normally.
- **`anomaly_detector.py`** restores any accidentally soft-deleted experiment before calling `set_experiment()`, so the pipeline never crashes on this condition regardless of what state deploy.sh left behind.
