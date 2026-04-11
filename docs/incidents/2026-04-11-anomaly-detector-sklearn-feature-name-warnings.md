# Incident: anomaly_detector.py — sklearn + MLflow Warnings on Step 7 Dry-Run

**Date:** 2026-04-11
**Severity:** Low (warnings only — no crash, no data loss)
**Status:** Fixed

---

## What Happened

During Step 7 of `docs/verification-steps.md` (dry-run of `anomaly_detector.py`), the pass criteria requires **zero WARNING lines** in the output. The run completed with valid JSON output and no tracebacks, but three `UserWarning` lines appeared, causing a soft fail:

```
/opt/ml-venv/lib/python3.12/site-packages/sklearn/base.py:486: UserWarning:
    X has feature names, but IsolationForest was fitted without feature names
  warnings.warn(

/opt/ml-venv/lib/python3.12/site-packages/mlflow/types/utils.py:406: UserWarning:
    Hint: Inferred schema contains integer column(s). Integer columns in Python
    cannot represent missing values. ...
  warnings.warn(

/opt/ml-venv/lib/python3.12/site-packages/sklearn/base.py:486: UserWarning:
    X has feature names, but IsolationForest was fitted without feature names
  warnings.warn(
```

The first sklearn warning appeared twice (once per MLflow internal call during model logging).

A second issue was visible but unrelated to the warnings: the `kubectl get pods | grep mlflow` output showed 14 dead pods (Evicted, Error, ContainerStatusUnknown) cluttering the namespace, left over from prior ephemeral storage eviction incidents.

---

## How the Warnings Were Encountered and Identified

The verification step `docs/verification-steps.md Step 7` was run manually via `kubectl exec`. The pass criteria explicitly states "No WARNING lines" — the three warnings were immediately visible in the terminal output and caused a clear fail of that criterion.

---

## Root Causes

### Warning 1 — "fitted without feature names" (sklearn, appears twice)

**Code location:** `anomaly_detector.py`, `run_model()`, original line 84:
```python
features = df[["revenue_yoy_pct", "net_income_yoy_pct"]].values  # .values strips to numpy array
model.fit(features)  # fitted on numpy — sklearn does NOT record column names
```

When a scikit-learn model is fitted on a **numpy array**, sklearn does not store feature names. Later, when `mlflow.sklearn.log_model()` is called with an `input_example` that is a **DataFrame** (which has named columns), MLflow internally calls `model.predict(input_example)` and `model.score_samples(input_example)` to infer the model signature. Since those calls pass a DataFrame to a model fitted on numpy, sklearn detects the mismatch and warns — twice (once per internal call).

### Warning 2 — "integer column(s)" (MLflow)

**Code location:** `anomaly_detector.py`, `run_model()`, original lines 120–121:
```python
input_ex = pd.DataFrame(features[:5], columns=["revenue_yoy_pct", "net_income_yoy_pct"])
```

`features` was a numpy array from `.values`. When wrapped back into a DataFrame via `pd.DataFrame(numpy_array, ...)`, if the underlying float values in the first 5 rows happen to be whole numbers (e.g., `2.0`, `-1.0`), numpy may infer the dtype as `int64` rather than `float64`. MLflow's schema inference then warns that integer columns cannot represent NaN at inference time.

### Dead pods

Leftover from ephemeral storage eviction incidents documented in `docs/incidents/2026-04-10-mlflow-ephemeral-storage-eviction.md`. The deploy script had no cleanup step to remove them after each deploy.

---

## Fix

### Fix 1 — Fit on DataFrame, not numpy (silences both sklearn warnings)

**File:** `airflow/dags/anomaly_detector.py`

```python
# Before:
features = df[["revenue_yoy_pct", "net_income_yoy_pct"]].values
model.fit(features)
df["is_anomaly"] = model.predict(features) == -1
df["anomaly_score"] = model.score_samples(features)
input_ex = pd.DataFrame(features[:5], columns=["revenue_yoy_pct", "net_income_yoy_pct"])

# After:
features_df = df[["revenue_yoy_pct", "net_income_yoy_pct"]]  # DataFrame preserves column names
model.fit(features_df)
df["is_anomaly"] = model.predict(features_df) == -1
df["anomaly_score"] = model.score_samples(features_df)
input_ex = features_df.iloc[:5].astype("float64")  # explicit float64 prevents integer-schema warning
```

### Fix 2 — Dead pod cleanup in deploy.sh

**Files:** `scripts/deploy/mlflow.sh` (new function), `scripts/deploy.sh` (call added between Step 7c and 7d)

Added `step_cleanup_dead_pods()`:
```bash
kubectl delete pods -n airflow-my-namespace --field-selector=status.phase=Failed --ignore-not-found=true
kubectl delete pods -n airflow-my-namespace --field-selector=status.phase=Unknown --ignore-not-found=true
```

---

## Why These Fixes Work

**Fix 1:** When both `fit()` and `predict()` use DataFrames, sklearn records the column names at fit time and confirms they match at predict time — no mismatch, no warning. The explicit `.astype("float64")` on the input example guarantees the dtype is floating point regardless of the values in the first 5 rows, so MLflow's schema inference always sees `float64` and never infers `int64`.

**Fix 2:** `status.phase=Failed` covers Evicted and Error pods (Kubernetes marks both as Failed phase). `status.phase=Unknown` covers ContainerStatusUnknown. `--ignore-not-found=true` makes it safe to run when there is nothing to delete.

---

## Verification

Re-run `docs/verification-steps.md Step 7`:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

Pass: zero `UserWarning` lines; last line of stdout is valid JSON with `n_anomalies`, `n_total`, `mlflow_run_id`.
