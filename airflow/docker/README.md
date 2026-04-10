# Dockerfile — Bug Fix: `pkg_resources` ModuleNotFoundError in ml-venv

## The Error

After deploying the MLflow integration, running mlflow inside the Airflow scheduler pod failed:

```
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/mlflow --version

Traceback (most recent call last):
  ...
  File "/opt/ml-venv/lib/python3.12/site-packages/mlflow/utils/requirements_utils.py", line 20, in <module>
    import pkg_resources
    ^^^^^^^^^^^^^^^^^^^^
ModuleNotFoundError: No module named 'pkg_resources'
```

## Root Cause

`pkg_resources` is part of the `setuptools` package — not the Python standard library. Before Python 3.12, `setuptools` was automatically included when creating a virtualenv (`python3 -m venv`), so `pkg_resources` was always available without explicitly installing it.

Starting with Python 3.12, Python removed `setuptools` from the default venv seed packages. The Airflow base image (`apache/airflow:3.1.8`) runs Python 3.12, so any venv created without explicitly installing `setuptools` will be missing `pkg_resources`. MLflow imports it deep in its import chain (`mlflow.utils.requirements_utils`), so even a simple `mlflow --version` triggers the failure.

## How It Was Identified

All other verification steps passed (pod Running, health endpoint returning `OK`, PVC Bound). The error surfaced on the final check:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/mlflow --version
```

The traceback pointed directly to the missing module, and the import path confirmed this was a dependency gap in the venv, not a problem with MLflow itself.

## Fix Part 1 — Dockerfile

Added `setuptools` as an explicit install in the `ml-venv` build step:

```dockerfile
# setuptools provides pkg_resources, which MLflow imports — not bundled by default in Python 3.12+
RUN python3 -m venv /opt/ml-venv \
    && /opt/ml-venv/bin/pip install --upgrade pip \
    && /opt/ml-venv/bin/pip install \
         setuptools \
         mlflow==2.15.1 \
         ...
```

This is the correct minimal fix — it restores the behavior that existed implicitly in Python < 3.12 without changing any pinned package versions or affecting the separate `dbt-venv`.

## Complication: K3S Containerd Image Cache

Adding `setuptools` to the Dockerfile and running `./scripts/deploy.sh` (which already uses `--no-cache` on `docker build`) was not enough on the first attempt. The scheduler pod restarted but the error persisted.

**Why:** `docker save image:tag | k3s ctr images import -` does not reliably update an existing tag in K3S containerd. The tag `docker.io/library/airflow-dbt:3.1.8-dbt` remained bound to the old image digest — the one without `setuptools`. When the pod restarted with `imagePullPolicy: Never`, K3S used the cached old image, not the newly built one.

## Fix Part 2 — `scripts/deploy.sh`

Added an explicit tag removal from K3S containerd before the import in Step 2b2:

```bash
# Remove stale K3S image tag so import below cleanly replaces it
sudo k3s ctr images rm docker.io/library/airflow-dbt:3.1.8-dbt 2>/dev/null || true &&
docker save airflow-dbt:3.1.8-dbt | sudo k3s ctr images import - &&
```

The `|| true` prevents failure if the image doesn't exist yet (first deploy). Removing the tag does not affect the currently running pod — containerd reference-counts image layers, so the existing container keeps its layers until it terminates. The import then creates a clean tag entry pointing to the new image. The pod restart in Step 7 picks up the fixed image.

## How the Fix Solves the Problem

When the Docker image is rebuilt (`--no-cache` forces a full layer rebuild), pip installs `setuptools` into `/opt/ml-venv/site-packages`. The stale K3S tag is removed before import, so the new image digest is the only one bound to `airflow-dbt:3.1.8-dbt`. When the scheduler pod restarts, K3S finds the new image, mounts the venv containing `setuptools`, and `import pkg_resources` resolves successfully.
