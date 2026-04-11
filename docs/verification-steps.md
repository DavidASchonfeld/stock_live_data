# Verification Steps: Anomaly Detection Pipeline (Thread 1 ML)

Run these steps **in order** inside the EC2 instance after deploying via `./scripts/deploy.sh`.

---

## Step 1 — Pods are Running

```bash
kubectl get pods -n airflow-my-namespace
```

**Pass:** All pods show `Running` or `Completed`. No `CrashLoopBackOff` or `Pending`.

---

## Step 2 — MLflow Pod is Healthy

```bash
kubectl get pods -n airflow-my-namespace | grep mlflow
```

**Pass:** MLflow pod is `Running` with `1/1` READY.

---

## Step 3 — ml-venv Exists in Scheduler Pod

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/pip list | grep -E "scikit-learn|mlflow|snowflake"
```

**Pass:** All three packages appear with version numbers. No `WARNING` about `/tmp/.cache/pip` (fixed in Dockerfile: `/tmp/.cache/pip` is now owned by the airflow user before `USER airflow` switches).

> **If you still see the WARNING:** the image pre-dates the Dockerfile fix — run `./scripts/deploy.sh` (full redeploy) to rebuild and import the new image.

---

## Step 4 — MLFLOW_TRACKING_URI Variable is Set

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables get MLFLOW_TRACKING_URI
```

**Pass:** Returns `http://mlflow.airflow-my-namespace.svc.cluster.local:5500` (no error).

---

## Step 5 — DAG Parses Cleanly (no import errors)

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list | grep stock_consumer
```

**Pass:** `stock_consumer_pipeline` appears **at least once** without errors.

> **Duplicate rows are expected.** Airflow 3.x runs multiple parallel DAG file processor workers; each one registers the DAG independently, producing one row per processor in `dags list`. This is normal display behavior and does not cause the DAG to execute more than once per trigger.

---

## Step 6 — Task List Shows detect_anomalies

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks list stock_consumer_pipeline
```

**Pass:** Output contains all six tasks:
```
check_new_rows
consume_from_kafka
dbt_run
dbt_test
detect_anomalies
write_to_snowflake
```

---

## Step 7 — Dry-run anomaly_detector.py Manually

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

**Pass:**
- **No WARNING lines** in the output (after fix: `input_example` is now provided to `log_model`)
- Last line of stdout is a valid JSON summary, e.g.:
  ```json
  {"n_anomalies": 1, "n_total": 16, "mlflow_run_id": "8a3ea1140d324e059ffd043f4876979b"}
  ```
- No Python tracebacks

---

## Step 8 — Trigger Full DAG End-to-End

> **Why do dbt_run, dbt_test, and detect_anomalies show as "skipped"?**
>
> This is intentional cost-saving behavior, not a bug. The pipeline has a **daily batch gate** inside `write_to_snowflake()`: if data was already written to Snowflake today, that task returns 0 rows and a `ShortCircuitOperator` (`check_new_rows`) skips everything downstream — dbt and anomaly detection included. This prevents Snowflake from being hit multiple times per day, which would get expensive fast.
>
> **To verify MLflow end-to-end without paying for another Snowflake write**, skip Step 8 and use Steps 7 → 9 → 10 instead. Step 7 runs `anomaly_detector.py` directly in the scheduler pod, which reads the data already in Snowflake (cheap SELECT), fits the model, logs to MLflow, and writes the small results table — all without triggering the daily gate.
>
> **To force a true full end-to-end DAG run** (including dbt), you need to reset the gate variable first. You can do this in two ways:
> - **Airflow UI (easier):** Admin → Variables → find `SF_STOCKS_LAST_WRITE_DATE` → edit → clear the value → Save.
> - **kubectl:** `kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow variables set SF_STOCKS_LAST_WRITE_DATE ""`
>
> After resetting, trigger the DAG and the gate will allow one full write through. It automatically reseals once the run completes.

**Trigger the DAG:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger stock_consumer_pipeline
```

**Check DAG run state** (poll until no longer `running`):
```bash
# Airflow 3.x: dag_id is a positional argument, not a flag
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs stock_consumer_pipeline
```

**Pass:** The `state` column shows `success`.

**If state shows `failed`**, check which task failed:
```bash
# Replace <run_id> with the run_id from the list-runs output above
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks states-for-dag-run stock_consumer_pipeline <run_id>
```
Then inspect that task's logs in the Airflow UI.

---

## Step 9 — Verify FCT_ANOMALIES in Snowflake

Run in a Snowflake worksheet:
```sql
SELECT COUNT(*), MAX(detected_at), MAX(mlflow_run_id)
FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES;
```

**Pass criteria:**
- `COUNT(*)` > 0 (rows exist)
- `MAX(detected_at)` is **today** (e.g. `2026-04-11`) — confirms the DELETE+INSERT from
  this DAG run executed; the count alone is not enough because the table full-refreshes
  and the underlying data may not have changed
- `MAX(mlflow_run_id)` matches the `mlflow_run_id` from the `detect_anomalies` task log

**Where to find the mlflow_run_id in Airflow UI:**
1. Open the Airflow UI → click the `stock_consumer_pipeline` DAG
2. Click the most recent DAG run → click the `detect_anomalies` task box
3. Open the **Logs** tab
4. Scroll to the last line of stdout — it is a JSON dict, e.g.:
   ```json
   {"n_anomalies": 1, "n_total": 16, "mlflow_run_id": "b3399a83b15b4c23ad5cac3eb294bebc"}
   ```
5. The `mlflow_run_id` value is what `MAX(mlflow_run_id)` in Snowflake must equal.

> **Note on row count:** `write_results()` does DELETE + INSERT on every run, so the row
> count will stay ~16 as long as the source data in `FCT_COMPANY_FINANCIALS` hasn't
> changed. That is expected. The `detected_at` timestamp is the authoritative proof of a
> fresh write.

---

## Step 10 — Verify MLflow UI

Open an SSH tunnel, then visit the UI:
```bash
ssh -L 5500:localhost:5500 ec2-stock
```

**Navigate directly to the specific run** using the `mlflow_run_id` from Step 9 above — this bypasses the experiment list page, which has a known MLflow 2.15.1 React crash when artifact metadata is partially unavailable:
```
http://localhost:5500/#/experiments/1/runs/<mlflow_run_id>
```

**Pass — walk through each of these in order:**

1. **Metrics are visible on the run page.**
   The main run page (the URL you navigated to) has a **Metrics** section roughly halfway down.
   You should see three rows: `n_anomalies`, `n_total`, and `contamination_rate`, each with a
   numeric value.

2. **isolation_forest artifact is present.**
   Click the **Artifacts** tab near the top of the run page. A left-side panel appears showing
   a file tree. You should see a folder called `isolation_forest`.

3. **Model signature shows the two input columns.**
   MLflow 2.15.1 has no "Schema" tab. The signature lives inside the `MLmodel` file:
   - In the Artifacts left panel, expand the `isolation_forest` folder (click the triangle).
   - Click **MLmodel**. The right panel shows raw YAML.
   - Scroll to the bottom of the YAML and find the `signature:` key.
   - Under `inputs:` you should see both columns listed:
     ```
     inputs: '[{"type": "double", "name": "revenue_yoy_pct", "required": true},
               {"type": "double", "name": "net_income_yoy_pct", "required": true}]'
     ```
   This confirms the model was logged with the correct input schema.

> **If the UI shows "no data" or crashes** the port-forward process likely dropped. Run `./scripts/deploy.sh` to restart it (the deploy script re-establishes the port-forward automatically). If the MLflow pod itself needs to be restarted (e.g., after a storage eviction), run the recovery function directly:
> ```bash
> # On your Mac — sources the deploy modules then calls the recovery step
> source scripts/deploy/common.sh && source scripts/deploy/mlflow.sh && step_restart_mlflow_pod
> ```
