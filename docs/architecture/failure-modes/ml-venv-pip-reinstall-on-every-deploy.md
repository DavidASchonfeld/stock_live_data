# Failure Mode: ml-venv Setup Fails on Every Deploy

**Status:** Fixed  
**Date:** 2026-04-11  
**Affected component:** `scripts/deploy/airflow_pods.sh` — `step_setup_ml_venv`

---

## What Happened

Every deploy ended with:

```
WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved.
```

The `step_setup_ml_venv` function (Step 7b) was consistently failing, meaning the anomaly detection DAG task would error at runtime when it tried to invoke `/opt/ml-venv/bin/python`.

---

## Root Cause

`step_setup_ml_venv` was unconditionally running `pip install --no-cache-dir` with 7 packages (including `mlflow==2.15.1`, which has dozens of transitive dependencies) on every single deploy — even when the Docker image already had a healthy, fully-installed `/opt/ml-venv`.

The logic was:
1. Reinitialize `/opt/ml-venv` with `python3 -m venv`
2. Download and install all packages from PyPI with `--no-cache-dir`
3. Verify imports

Step 2 was the problem. Installing `mlflow`, `scikit-learn`, `snowflake-connector-python`, and their transitive deps from scratch — with no pip cache — takes several minutes and is fragile. Any PyPI network hiccup, rate limit, or timeout would fail the step.

The key insight: the Dockerfile (`airflow/docker/Dockerfile`) already bakes `/opt/ml-venv` with all packages into the Docker image at build time. When a pod restarts from the same image, the venv is already there and working. There was no need to reinstall anything.

---

## Fix

Two changes:

### 1. `scripts/deploy/airflow_pods.sh` — verify-first fast path

Added an import check at the start of `step_setup_ml_venv`. If the Docker-baked venv already works, the step completes in under 2 seconds and skips the pip install entirely. The full install is now a fallback only triggered when the venv is actually missing or broken (e.g., image out of sync, container filesystem corruption).

### 2. `airflow/docker/Dockerfile` — align ml-venv packages

Added `requests>=2.32.0` and `chardet>=3.0.2,<6` to the ml-venv `RUN` block. These were previously only installed into Airflow's main Python environment, but the fallback install in `airflow_pods.sh` included them in ml-venv. The Dockerfile now matches the fallback exactly so the fast path and fallback install the same packages.

---

## Result

- Normal deploys: Step 7b completes in `<5s` (import check passes, no install)
- Edge-case deploys (broken venv): Step 7b falls back to the full install with clear logging
- The "ml-venv setup failed" warning is gone from normal deploy output

---

## How to Diagnose If It Recurs

```bash
# Check if venv exists and packages are importable
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python -c 'import sklearn, mlflow, snowflake.connector, pandas; print("OK")'

# List installed packages in the venv
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/pip list

# Check if the venv directory exists at all
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- ls /opt/ml-venv/bin/
```

If the fallback install itself fails, the most likely causes are:
- PyPI network timeout from EC2 (retry or check outbound connectivity)
- A transitive dependency conflict introduced by a package version bump
- Disk space exhausted on the EC2 node
