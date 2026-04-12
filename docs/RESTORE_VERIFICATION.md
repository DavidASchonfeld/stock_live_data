# Restore Verification Checklist

Run these steps **in order** after restoring the project from scratch (Terraform + Helm deploy via `./scripts/deploy.sh`). Each section has a clear **Pass** condition. If a step fails, fix it before continuing — later steps depend on earlier ones.

> **Context:** This doc covers the full pipeline. For deep dives into specific components, see:
> - Anomaly detection + MLflow details → [`docs/verification-steps.md`](verification-steps.md)
> - OpenLineage details → [`docs/operations/T3_OPENLINEAGE_VERIFY.md`](operations/T3_OPENLINEAGE_VERIFY.md)

---

## Step 1 — All Pods Running

```bash
kubectl get pods -n airflow-my-namespace
kubectl get pods -n kafka
```

**Pass:** Every pod shows `Running` or `Completed`. No `CrashLoopBackOff`, `Pending`, or `ImagePullBackOff`.

Expected pods in `airflow-my-namespace`: scheduler, api-server (Airflow 3.x name for webserver), triggerer, postgres, mlflow (and any workers).
Expected pod in `kafka`: `kafka-0` with `1/1` READY.
The Flask dashboard runs in the **`default`** namespace as `my-kuber-pod-flask`, not in `airflow-my-namespace`.

---

## Step 2 — PersistentVolumes Mounted

```bash
kubectl get pvc -n airflow-my-namespace
kubectl get pvc -n kafka
```

**Pass:** All PVCs show `Bound`. If any are `Pending`, the underlying EBS volumes may not have attached yet — wait 60s and retry.

---

## Step 3 — Snowflake Connection

Verify Airflow has the Snowflake connection:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow connections get snowflake_default
```

**Pass:** Prints connection details without `Connection not found` error. Verify the output shows a non-empty `account` field in `extra_dejson` (e.g. `"account": "qztxwkd-lsc26305"`). SnowflakeHook 6.x reads the account from `extra.account`, not the `host` field — if `account` is blank, the deploy script's JSON connection format may not have applied; re-run `./scripts/deploy.sh` and restart the scheduler pod.

Then confirm the target schemas exist (run in a Snowflake worksheet):

```sql
SHOW SCHEMAS IN DATABASE PIPELINE_DB;
```

**Pass:** Four schemas visible: `RAW`, `STAGING`, `MARTS`, `ANALYTICS`.

> If schemas are missing, re-run the Snowflake setup SQL in [`docs/architecture/SNOWFLAKE_SETUP.md`](architecture/SNOWFLAKE_SETUP.md).

---

## Step 4 — Kafka Topics Exist

```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-topics.sh --list --bootstrap-server localhost:9092
```

**Pass:** Output contains both:
- `stocks-financials-raw`
- `weather-hourly-raw`

If topics are missing, they are created automatically on first producer DAG run — you can also create them manually:

```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-topics.sh --create --topic stocks-financials-raw \
    --bootstrap-server localhost:9092 --partitions 1 --replication-factor 1 \
    --config retention.ms=172800000 --config retention.bytes=104857600

kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-topics.sh --create --topic weather-hourly-raw \
    --bootstrap-server localhost:9092 --partitions 1 --replication-factor 1 \
    --config retention.ms=172800000 --config retention.bytes=104857600
```

---

## Step 5 — Airflow Variables Set

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables list
```

**Pass:** Output includes at minimum:
- `MLFLOW_TRACKING_URI` → `http://mlflow.airflow-my-namespace.svc.cluster.local:5500`
- `VACATION_MODE` → `false` (or intentionally `true` if you don't want DAGs to run)
- `SF_STOCKS_LAST_WRITE_DATE` → blank or a past date

> If `MLFLOW_TRACKING_URI` is missing, it means the deploy script's variable-setup step failed. Re-run `./scripts/deploy.sh` or set it manually:
> ```bash
> kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
>     airflow variables set MLFLOW_TRACKING_URI "http://mlflow.airflow-my-namespace.svc.cluster.local:5500"
> ```

---

## Step 6 — All 5 DAGs Parse Without Errors

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list
```

**Pass:** All five DAGs appear (duplicate rows are normal in Airflow 3.x — each processor worker registers separately):
- `Stock_Market_Pipeline`
- `stock_consumer_pipeline`
- `API_Weather-Pull_Data`
- `weather_consumer_pipeline`
- `Data_Staleness_Monitor`

Check for import errors:

```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace | grep -i "import error\|broken dag" | tail -20
```

**Pass:** No import error lines.

---

## Step 7 — dbt Is Functional

Verify the dbt venv is present:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "/opt/dbt-venv/bin/dbt --version 2>&1"
```

**Pass:** Prints dbt version (1.8.x).

> **Why `bash -c "... 2>&1"`?** dbt 1.8.x writes all CLI output to stderr. `kubectl exec` without a TTY separates stdout/stderr — stderr is silently discarded. Wrapping in `bash -c "... 2>&1"` merges the streams so output reaches your terminal. All dbt commands in this section use this pattern.

Confirm the `dbt-profiles` secret is mounted before running compile:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "ls /dbt/profiles.yml 2>&1"
```

**Pass:** Prints `/dbt/profiles.yml`. If "No such file or directory", the secret isn't mounted — re-run `./scripts/deploy.sh` and wait for the pod to restart.

Run the compile check with the same env vars the DAG tasks use. `--debug` forces dbt to print to the terminal even when `DBT_LOG_PATH` is set (without it, dbt 1.8.x routes everything to the log file and the terminal shows nothing):

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "mkdir -p /tmp/dbt_target /tmp/dbt_logs && \
    DBT_PROFILES_DIR=/dbt \
    DBT_TARGET_PATH=/tmp/dbt_target \
    DBT_LOG_PATH=/tmp/dbt_logs \
    /opt/dbt-venv/bin/dbt --debug compile \
    --project-dir /opt/airflow/dags/dbt \
    --select tag:stocks \
    --no-use-colors 2>&1"
```

**Pass:** Exits 0 with `Command 'dbt compile' succeeded` — confirms profiles.yml connects, Snowflake credentials are valid, and all SQL is parseable.

> If this exits non-zero, the debug output printed above will show the exact error.
> - **Profiles not found**: re-run `./scripts/deploy.sh` (sync.sh Step 2c2 recreates the `dbt-profiles` secret).
> - **Snowflake auth error**: check `kubectl get secret snowflake-credentials -n airflow-my-namespace` and compare against `.env.deploy.example`.

---

## Step 8 — End-to-End: Stocks Pipeline

This is the main verification. Two things must be done **before** triggering the producer:

**1. Reset the Kafka consumer group offset** (required on fresh deploy or after any Kafka pod restart — the consumer uses `auto_offset_reset="latest"`, so without a committed offset it starts at the topic end and misses newly produced messages):

```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group stocks-consumer-group \
    --reset-offsets --to-latest \
    --topic stocks-financials-raw --execute
```

**2. Reset the daily batch gate** (prevents the ShortCircuitOperator from skipping dbt + anomaly detection if this pipeline already ran today):

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set SF_STOCKS_LAST_WRITE_DATE ""
```

Now trigger the full pipeline:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger Stock_Market_Pipeline
```

Poll until complete (wait ~5–10 min for SEC EDGAR fetch + Kafka + Snowflake + dbt + anomaly detection):

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs Stock_Market_Pipeline
```

**Pass:** `state = success` for `Stock_Market_Pipeline`. Then check the consumer was auto-triggered:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs stock_consumer_pipeline
```

**Pass:** `state = success` for `stock_consumer_pipeline`.

Verify all 6 consumer tasks ran (not skipped):

```bash
# Replace <run_id> with the run_id from list-runs above
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks states-for-dag-run stock_consumer_pipeline <run_id>
```

**Pass:** `consume_from_kafka`, `write_to_snowflake`, `check_new_rows`, `dbt_run`, `dbt_test`, `detect_anomalies` all show `success`.

> If `dbt_run` / `dbt_test` / `detect_anomalies` show `skipped` — the `check_new_rows` gate fired (0 rows written). The most common causes are: (1) Kafka consumer offset not reset before triggering (consumer missed the message), or (2) the `write_to_snowflake` Snowflake connection failed — check the file logger at `/opt/airflow/out/` for the most recent log files.

---

## Step 9 — Verify Snowflake Data (Stocks)

Run in a Snowflake worksheet:

```sql
-- Raw data loaded by Kafka consumer
SELECT COUNT(*) FROM PIPELINE_DB.RAW.COMPANY_FINANCIALS;

-- dbt mart built from staging
SELECT COUNT(*), MAX(period_end) FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS;

-- Dimension table
SELECT * FROM PIPELINE_DB.MARTS.DIM_COMPANY;

-- Anomaly detection results — detected_at should be TODAY
SELECT COUNT(*), MAX(detected_at), MAX(mlflow_run_id)
FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES;
```

**Pass:**
- `RAW.COMPANY_FINANCIALS` row count > 0
- `MARTS.FCT_COMPANY_FINANCIALS` row count > 0, `MAX(period_end)` is a recent date
- `DIM_COMPANY` shows 3 rows: AAPL, MSFT, GOOGL
- `FCT_ANOMALIES` row count > 0, `MAX(detected_at)` = today

---

## Step 10 — End-to-End: Weather Pipeline

Same pre-requisites as Step 8 — reset Kafka offset and date gate **before** triggering:

```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group weather-consumer-group \
    --reset-offsets --to-latest \
    --topic weather-hourly-raw --execute

kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set SF_WEATHER_LAST_WRITE_DATE ""
```

Then trigger:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger API_Weather-Pull_Data
```

Poll until complete (~2–3 min):

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags list-runs API_Weather-Pull_Data
```

**Pass:** `state = success`. Then confirm in Snowflake:

```sql
SELECT COUNT(*), MAX(imported_at) FROM PIPELINE_DB.RAW.WEATHER_HOURLY;
SELECT COUNT(*), MAX(time) FROM PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY;
```

**Pass:** Both have rows, `MAX(imported_at)` and `MAX(time)` are recent.

---

## Step 11 — MLflow Pod and UI

```bash
kubectl get pods -n airflow-my-namespace | grep mlflow
```

**Pass:** MLflow pod is `Running` with `1/1` READY.

Open SSH tunnel and check UI:

```bash
ssh -L 5500:localhost:5500 ec2-stock
```

Navigate to `http://localhost:5500/#/experiments/1` — you should see at least one completed run from Step 8.

> For a detailed MLflow verification (metrics, model signature, artifact tree), follow Steps 9–10 in [`docs/verification-steps.md`](verification-steps.md).

---

## Step 12 — OpenLineage Emitting Events

After Step 8, check the `dbt_run` task logs in the Airflow UI:

1. Airflow UI → `stock_consumer_pipeline` → most recent run → `dbt_run` task → **Logs**
2. Search for `"eventType"` in the log output

**Pass:** JSON blocks appear like:
```json
{"eventType": "START", "job": {"namespace": "pipeline", ...}, "inputs": [...], "outputs": [...]}
```
One START + COMPLETE pair per dbt model.

Or verify from the command line:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/dbt-venv/bin/pip show openlineage-dbt
```

**Pass:** Prints `Name: openlineage-dbt` with a version number.

> For full OpenLineage verification steps, see [`docs/operations/T3_OPENLINEAGE_VERIFY.md`](operations/T3_OPENLINEAGE_VERIFY.md).

---

## Step 13 — Dashboard Loads and Queries Data

Check the Flask liveness endpoint:

```bash
curl http://<EC2_PUBLIC_IP>:32147/health
```

**Pass:** Returns `{"status": "ok"}` with HTTP 200.

Open the dashboard in a browser: `http://<EC2_PUBLIC_IP>:32147/dashboard/`

**Pass:**
- Dropdown shows AAPL, MSFT, GOOGL
- Selecting a ticker renders the candlestick chart and stats table
- **Data Quality** tab shows the anomaly scatter plot (rows from FCT_ANOMALIES)
- Weather tab at `/weather/` renders the hourly forecast charts

Check the validation endpoint for row counts and freshness:

```bash
curl http://<EC2_PUBLIC_IP>:32147/validation
```

**Pass:** Returns JSON with `"status": "ok"`, row counts > 0, and recent timestamps for both `company_financials` and `weather_hourly`.

---

## Step 14 — Staleness Monitor (Optional)

The staleness monitor is paused by default (to save Snowflake compute costs — leave it paused unless you need to verify alerting). Trigger it directly without unpausing:

> **Note:** `airflow dags trigger` works on paused DAGs. Do not unpause it permanently.

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger Data_Staleness_Monitor
```

**Pass:** DAG completes with `success` (check `airflow dags list-runs Data_Staleness_Monitor`).

> **Expected alert behaviour after Steps 8–10:**
> - **Stocks** (`FCT_COMPANY_FINANCIALS`): The `filed_date` column reflects the actual SEC EDGAR filing date (e.g. Feb 2026 for FY2025 10-Ks), not the pipeline run date. This will almost always exceed the 168h threshold. The alert is expected and does not indicate a pipeline problem.
> - **Weather** (`FCT_WEATHER_HOURLY`): Should be fresh (within 2h) if you just ran Steps 10 with the Kafka offset reset. If an alert fires here, the weather write to Snowflake may not have succeeded — re-check Step 10's task states.

---

## Quick Reference: What Each Step Verifies

| Step | Component | What it proves |
|------|-----------|---------------|
| 1 | Kubernetes | All pods are healthy |
| 2 | Kubernetes | Storage volumes attached |
| 3 | Snowflake | Connection works, schemas exist |
| 4 | Kafka | Topics exist, broker reachable |
| 5 | Airflow | Variables/secrets injected correctly |
| 6 | Airflow | All 5 DAGs parse without errors |
| 7 | dbt | Models compile, Snowflake credentials valid |
| 8 | Stocks pipeline | Full extract → Kafka → Snowflake → dbt → ML run succeeds |
| 9 | Snowflake | Data populated in RAW, MARTS, ANALYTICS |
| 10 | Weather pipeline | Full weather extract → Kafka → Snowflake → dbt run succeeds |
| 11 | MLflow | Experiment tracking pod up, run logged |
| 12 | OpenLineage | Lineage events emitting from dbt runs |
| 13 | Dashboard | UI loads, charts render, data is fresh |
| 14 | Alerting | Staleness monitor fires correctly |
