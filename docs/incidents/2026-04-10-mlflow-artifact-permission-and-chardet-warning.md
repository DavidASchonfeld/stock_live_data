# Incident: MLflow Artifact PermissionError + chardet RequestsDependencyWarning

**Date:** 2026-04-10
**Severity:** Blocking (PermissionError crashes anomaly_detector.py) + Non-blocking (warning noise)

---

## Errors

```
PermissionError: [Errno 13] Permission denied: '/mlflow-data'
```
```
RequestsDependencyWarning: urllib3 (2.6.3) or chardet (6.0.0.post1)/charset_normalizer (3.4.5)
doesn't match a supported version!
```

---

## How They Were Encountered

Running the post-deploy verification steps against the live scheduler pod:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

The `dags list` command produced the `RequestsDependencyWarning` on every invocation. The `anomaly_detector.py` run succeeded through data fetching and model training, logged params and metrics to MLflow, then crashed at `mlflow.sklearn.log_model()` with `PermissionError: '/mlflow-data'`.

---

## Issue 1 — PermissionError: `/mlflow-data`

### Root Cause

The MLflow server was started with `--default-artifact-root /mlflow-data/artifacts` — a local filesystem path. When an experiment is created, MLflow bakes that path into the run metadata stored in SQLite as the `artifact_location`. When `mlflow.sklearn.log_model()` runs on the Airflow scheduler pod, the MLflow client reads that URI, sees a `file://` path, and tries to write directly to `/mlflow-data/artifacts/...` on the scheduler pod's local filesystem.

`/mlflow-data` does not exist on the scheduler pod. It only exists inside the MLflow server pod, where the PVC is mounted. Despite `--serve-artifacts` being set on the server, the artifact write path is determined by the URI stored in the run metadata — and since that URI was a local path, the client bypassed the HTTP proxy entirely and attempted a direct filesystem write.

### How It Was Identified

The traceback pointed to `mlflow.store.artifact.local_artifact_repo.LocalArtifactRepository` — the class MLflow uses when the artifact URI is a local path (not an `s3://`, `gs://`, or `mlflow-artifacts://` URI). This confirmed the client was never going through the HTTP proxy. The cause was the `--default-artifact-root /mlflow-data/artifacts` in `deployment-mlflow.yaml`.

### Fix

**`airflow/manifests/mlflow/deployment-mlflow.yaml`** — replaced the local-path artifact root with the `mlflow-artifacts:/` proxy scheme and added `--artifacts-destination` for the server-side storage path:

```yaml
# Before
- --default-artifact-root
- /mlflow-data/artifacts

# After
- --artifacts-destination    # server writes bytes here on the PVC
- /mlflow-data/artifacts
- --default-artifact-root    # clients see this proxied URI
- mlflow-artifacts:/
```

**`scripts/deploy.sh`** — added Step 7c to soft-delete the existing `anomaly_detection` experiment after the MLflow server restarts. This is required because the old experiment already had `artifact_location=/mlflow-data/artifacts` baked into SQLite; new runs in that experiment would still use the stale local path. Deleting it causes `mlflow.set_experiment("anomaly_detection")` to recreate it fresh with the new `mlflow-artifacts:/` root on the next run.

Also fixed: the `MLFLOW_TRACKING_URI` reminder in deploy.sh referenced port `5000` instead of the correct `5500`.

### Why This Fix

`mlflow-artifacts:/` is the MLflow-idiomatic way to configure artifact proxying. When `--serve-artifacts` is enabled and `--default-artifact-root` is `mlflow-artifacts:/`, the tracking server returns a proxied URI to clients, which then upload artifacts over HTTP to the server instead of writing directly to disk. The server stores the bytes at `--artifacts-destination`. This is the intended client/server separation — the scheduler pod never needs the PVC mounted.

The alternative (mounting the PVC into the scheduler pod as well) was rejected: the PVC is `ReadWriteOnce`, so two pods can't mount it simultaneously without switching to `ReadWriteMany`, which adds storage complexity for no benefit.

### How the Fix Solved the Problem

With `--default-artifact-root mlflow-artifacts:/`, new runs have their `artifact_uri` set to `mlflow-artifacts:/...`. The MLflow client sees that scheme, selects `MlflowArtifactRepository` (HTTP proxy) instead of `LocalArtifactRepository`, and uploads the model artifact via a POST to the MLflow server's artifact endpoint. The server receives the upload and writes it to `/mlflow-data/artifacts` on the PVC — entirely server-side, no direct filesystem access from the scheduler pod.

---

## Issue 2 — RequestsDependencyWarning

### Root Cause

`requests/__init__.py` contains a startup check that validates the versions of its dependencies. One check requires `chardet < 6`. The Airflow environment had `chardet 6.0.0.post1` installed — pulled in as a transitive dependency — which caused the check to fire on every Python process that imports `requests`, including the Airflow CLI.

An earlier attempt to fix this by upgrading `requests` to `>=2.32.0` did not resolve it because the version check in `requests/__init__.py` is keyed on `chardet`'s version, not `requests`' own version. Upgrading `requests` alone left `chardet 6.0.0.post1` in place, so the check still fired.

### How It Was Identified

The warning path (`requests/__init__.py:113`) pointed directly at the dependency version check. The warning message showed the three dependency versions (`urllib3 2.6.3`, `chardet 6.0.0.post1`, `charset_normalizer 3.4.5`). Of those three, only `chardet 6.0.0.post1` was outside the supported range (`< 6`). The fix was to downgrade chardet, not upgrade requests.

### Fix

**`airflow/docker/Dockerfile`** — added `chardet>=3.0.2,<6` to pin it within the supported range:

```dockerfile
# requests warns when chardet ≥6 is installed; pin chardet<6 to suppress the check
RUN pip install --no-cache-dir "requests>=2.32.0" "chardet>=3.0.2,<6"
```

**`scripts/deploy.sh`** — added the same pin to the Step 7b ml-venv install so the dynamically rebuilt venv matches the baked image.

### Why This Fix

Downgrading chardet to `<6` puts it back within the range `requests` considers supported, eliminating the check at its source. This is preferable to suppressing the warning with `PYTHONWARNINGS` or `warnings.filterwarnings()`, which would hide a real signal if a genuinely incompatible version were introduced later.

### How the Fix Solved the Problem

With `chardet<6` pinned, the version check in `requests/__init__.py` passes, and no warning is emitted. The fix is applied in both the Docker image (baked at build time) and the ml-venv (rebuilt at deploy time in Step 7b), so both execution contexts are consistent.
