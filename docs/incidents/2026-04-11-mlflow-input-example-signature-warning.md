# Incident: MLflow "Input Example" Signature Warning

**Date:** 2026-04-11
**Severity:** Low (warning only — script ran successfully)
**File affected:** `airflow/dags/anomaly_detector.py`

---

## What Happened

Running `anomaly_detector.py` manually (Step 7 of the verification checklist) produced:

```
WARNING mlflow.models.model: Input example should be provided to infer model signature
if the model signature is not provided when logging the model.
```

The script still completed and wrote results to Snowflake and MLflow, but the warning
indicated a best-practice gap.

---

## How It Was Identified

Observed during manual Step 7 dry-run:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

The warning appeared before the final JSON summary line.

---

## Root Cause

`mlflow.sklearn.log_model(model, "isolation_forest")` was called without an
`input_example` argument. MLflow needs a sample input to:
1. Infer the model's input/output schema (called the **model signature**)
2. Store that schema alongside the artifact so it can validate inputs at inference time
3. Display the schema in the MLflow UI under the artifact's "Schema" tab

Without it, MLflow logs the model but cannot determine column names or dtypes — hence the warning.

---

## Fix

Added `input_example` to the `log_model` call in `run_model()`, line ~120:

```python
# Before
mlflow.sklearn.log_model(model, "isolation_forest")

# After
input_ex = pd.DataFrame(features[:5], columns=["revenue_yoy_pct", "net_income_yoy_pct"])
mlflow.sklearn.log_model(model, "isolation_forest", input_example=input_ex)
```

- `features` was already in scope (numpy array built 2 lines above)
- Wrapped in a DataFrame with column names so MLflow stores named columns in the signature
- `[:5]` is the MLflow-recommended sample size for tabular models
- `pd` was already imported — no new imports required

---

## Why This Fix

Using a named DataFrame (rather than a raw numpy array) gives MLflow the column names
it needs to produce a typed signature (`revenue_yoy_pct: double`, `net_income_yoy_pct: double`).
This signature is stored with the artifact and enables safe model loading + input validation
downstream when the model is served or loaded via `mlflow.sklearn.load_model()`.

---

## How It Solved the Problem

MLflow's `log_model` now receives a concrete sample of the input data. It uses this to
call `infer_signature(input_example)` internally, serialize the schema, and attach it to
the artifact metadata. The warning is suppressed because the missing data is now provided.
