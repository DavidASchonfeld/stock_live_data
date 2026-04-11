# Incident: Anomaly Detection — 3 Errors on First Full Run

**Date:** 2026-04-10
**Severity:** Blocking (anomaly detection task fails entirely; warnings on every CLI command)

---

## Errors

```
RequestsDependencyWarning: urllib3 (2.6.3) or chardet (6.0.0.post1)/charset_normalizer (3.4.5)
doesn't match a supported version!
```

```
/opt/airflow/dags/anomaly_detector.py:67: FutureWarning: The default fill_method='ffill' in
DataFrameGroupBy.pct_change is deprecated and will be removed in a future version.
```

```
PermissionError: [Errno 13] Permission denied: '/mlflow-data'
```

---

## How They Were Encountered

All three surfaced during post-deploy verification — running `anomaly_detector.py` directly inside the scheduler pod:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

The `RequestsDependencyWarning` appeared on every `airflow` CLI invocation (steps 5–6 of the verification sequence). The `FutureWarning` and `PermissionError` appeared during the script run — the script successfully connected to Snowflake and MLflow, then crashed when attempting to log the model artifact.

---

## Root Causes

### 1 — `RequestsDependencyWarning`
The Airflow base image ships a `requests` version that validates urllib3/chardet against a hardcoded compatible range. urllib3 2.6.3 and chardet 6.x are newer than that range. The fix (`pip install "requests>=2.32.0"`) had already been added to the Dockerfile, but the image had not yet been rebuilt and redeployed.

### 2 — `FutureWarning: fill_method='ffill'`
`anomaly_detector.py:67` called `DataFrameGroupBy.pct_change()` with no arguments. pandas 2.x deprecated the implicit `fill_method='ffill'` default and will remove it in a future version, emitting a `FutureWarning` each run.

### 3 — `PermissionError: /mlflow-data`
The MLflow server was started with `--default-artifact-root /mlflow-data/artifacts` — a local host path inside the MLflow pod. When `mlflow.sklearn.log_model()` is called, the tracking server returns this path as the artifact URI. The MLflow *client* (running inside the scheduler pod) then tries to write to `/mlflow-data/artifacts` directly as a local filesystem path. That path doesn't exist on the scheduler pod, causing a `PermissionError`.

---

## How Each Was Identified

1. **`RequestsDependencyWarning`:** Warning text names the exact packages and versions. The stack path (`/home/airflow/.local/lib/python3.12/site-packages/requests/__init__.py:113`) confirmed it was Airflow's own Python env. The Dockerfile already had the fix — confirmed to be a deploy-lag issue only.

2. **`FutureWarning`:** Traceback pointed directly to `anomaly_detector.py:67`. The warning message named `fill_method='ffill'` as the deprecated default, making the fix unambiguous.

3. **`PermissionError`:** Full traceback showed the call chain: `mlflow.sklearn.log_model` → `log_artifacts` → `LocalArtifactRepo.log_artifacts` → `mkdir('/mlflow-data/...')`. The class `LocalArtifactRepo` was the key signal — it means the client resolved the artifact URI to a local filesystem path. That only happens when the tracking server returns a bare local path (`/mlflow-data/artifacts`) instead of an HTTP-proxied URI. Checking `deployment-mlflow.yaml` confirmed `--serve-artifacts` was absent from the server command.

---

## Fixes

### 1 — `RequestsDependencyWarning`
**`airflow/docker/Dockerfile`** — already fixed; no new code change. Redeploy rebuilds the image:
```dockerfile
# Upgrade requests ≥2.32.0 — removes urllib3/chardet version-mismatch warning
RUN pip install --no-cache-dir "requests>=2.32.0"
```

### 2 — `FutureWarning`
**`airflow/dags/anomaly_detector.py:67`**
```diff
- wide.groupby("ticker")[["revenue", "net_income"]].pct_change()
+ wide.groupby("ticker")[["revenue", "net_income"]].pct_change(fill_method=None)  # fill_method=None: explicit no-ffill, suppresses FutureWarning from deprecated default
```

### 3 — `PermissionError`
**`airflow/manifests/mlflow/deployment-mlflow.yaml`**
```diff
  - --default-artifact-root
  - /mlflow-data/artifacts
+ - --serve-artifacts          # proxy artifact uploads via HTTP — clients never need direct /mlflow-data access
  - --host
```

---

## Why These Fixes

- **requests upgrade:** requests 2.32+ removed the urllib3/chardet version check entirely — no need to pin individual transitive deps.
- **`fill_method=None`:** The pipeline sorts by `fiscal_year` before calling `pct_change()`, so rows are already in order and no forward-fill is needed. Passing `None` is both the correct behavior and the explicit, future-proof form pandas now requires.
- **`--serve-artifacts`:** The root problem was the MLflow client writing artifacts directly to the server's local disk. The purpose of a tracking server is to be the single intermediary. `--serve-artifacts` enforces that: clients always upload via HTTP, and the server handles writing to `/mlflow-data`. No other pod needs the PVC mounted.

---

## How the Fixes Solved the Problems

1. **requests:** requests 2.32+ has no version-check code at line 113 — the warning cannot fire.
2. **`fill_method=None`:** pandas no longer emits the `FutureWarning` when the argument is explicit.
3. **`--serve-artifacts`:** MLflow now returns an `mlflow-artifacts:/…` URI instead of `/mlflow-data/artifacts`. The client uploads the model via the MLflow HTTP API; the server writes to its own `/mlflow-data` volume. The scheduler pod never touches the filesystem directly.
