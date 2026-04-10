# Incident: `ModuleNotFoundError: No module named 'pkg_resources'` in ml-venv

**Date:** 2026-04-10
**Component:** Airflow scheduler pod — `/opt/ml-venv`

---

## Error

```
File "/opt/ml-venv/lib/python3.12/site-packages/mlflow/utils/requirements_utils.py", line 20
    import pkg_resources
ModuleNotFoundError: No module named 'pkg_resources'
```

## How It Was Encountered

During verification of the MLflow integration, the scheduler pod was exec'd into and mlflow was imported directly from the ml-venv:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python -c "import mlflow, sklearn; print(mlflow.__version__, sklearn.__version__)"
```

## Root Cause

Two compounding issues:

1. **Missing `setuptools`**: Python 3.12 no longer bundles `setuptools` in virtual environments by default. `pkg_resources` is provided by `setuptools`, so any venv created without explicitly installing it will fail when mlflow tries to import it.

2. **K3S containerd snapshot cache**: Even after adding `setuptools` to the Dockerfile and using `--no-cache` in `docker build`, re-deploying with the **same image tag** (`3.1.8-dbt`) didn't fix the running pod. K3S containerd caches unpacked image layer snapshots internally. Re-importing a tag that already exists can cause K3S to reuse old snapshot data rather than extracting the new image layers — so the scheduler pod kept running with the old, broken image content despite the image being rebuilt.

## Fix

Two changes to `scripts/deploy.sh`:

**1. Added `setuptools` to the Dockerfile** (`airflow/docker/Dockerfile`):
```dockerfile
RUN python3 -m venv /opt/ml-venv \
    && /opt/ml-venv/bin/pip install --upgrade pip \
    && /opt/ml-venv/bin/pip install \
         setuptools \    # provides pkg_resources for Python 3.12+
         mlflow==2.15.1 \
         ...
```

**2. Dynamic image tag per deploy** (`scripts/deploy.sh`):
```bash
BUILD_TAG="3.1.8-dbt-$(date +%Y%m%d%H%M%S)"
docker build --no-cache -t airflow-dbt:$BUILD_TAG ...
helm upgrade ... --set images.airflow.tag=$BUILD_TAG ...
```

## Why This Fix

A static tag with `pullPolicy: Never` gives K3S no indication that the image contents changed. K3S's containerd snapshot store is content-addressed — importing a new image over an existing tag can silently reuse previously-unpacked layer snapshots. A timestamp-based tag is guaranteed unseen by K3S, so it always creates fresh snapshots from the newly imported image layers. Combined with `--no-cache` on the Docker build, this eliminates caching at every level of the stack.
