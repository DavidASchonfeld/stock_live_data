# Incident: Restore Verification Audit — Three Silent Failures Found — Apr 12 2026

## What Happened

Ran through `docs/RESTORE_VERIFICATION.md` end-to-end for the first time since several recent infrastructure changes. The document had not been validated against the current state of the cluster. Three separate issues caused the pipeline to silently appear healthy while actually producing no new data — `dbt`, anomaly detection, and the staleness monitor had all been broken for an unknown period.

---

## Issue 1 — Kafka Consumers Always Missed New Messages

### Symptom

Every run of `stock_consumer_pipeline` and `weather_consumer_pipeline` for at least the past several days showed the same pattern in task states:

```
consume_from_kafka  → success
write_to_snowflake  → success (returned 0 rows)
check_new_rows      → success (ShortCircuit returned False)
dbt_run             → skipped
dbt_test            → skipped
detect_anomalies    → skipped
```

The DAGs showed `state = success` so nothing looked broken. File logger confirmed the truth:

```
consume_from_kafka: 0 records received from Kafka
write_to_snowflake: no records received from Kafka — skipping
```

### Root Cause

Both consumer groups (`stocks-consumer-group`, `weather-consumer-group`) had no committed offsets — Kafka reported `Group not found` when asked to describe them. Both consumers are configured with `auto_offset_reset="latest"` and `enable_auto_commit=False` (manual commit after each message). Without a committed offset, `auto_offset_reset="latest"` seeks to the end of the topic at the moment the consumer connects — which is always *after* any message the producer just published. The consumer polls for 30 seconds, finds nothing, and exits cleanly with 0 records. Since no messages are consumed, `consumer.commit()` is never called, so the group offset is never created, and every subsequent run repeats the same cycle.

### Why Resetting to Earliest Doesn't Work

An initial attempt to fix this by resetting the weather consumer group to `--to-earliest` (offset 0) failed with a `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`. The weather topic (`weather-hourly-raw`) contains 37 messages with at least one near offset 0 that has an empty or null value — leftover from an early run before the message format was stabilised. The stocks topic wasn't affected by this, but using `--to-earliest` on a topic with old messages is fragile in general.

### Fix

Reset both consumer groups to `--to-latest` *before* triggering the producer. This positions the consumer's committed offset at the current end of the topic. The producer then publishes a new message at the next offset, the consumer starts from its committed position and finds it.

The fix is now documented in `RESTORE_VERIFICATION.md` Steps 8 and 10:

```bash
kubectl exec kafka-0 -n kafka -- \
    /opt/kafka/bin/kafka-consumer-groups.sh \
    --bootstrap-server localhost:9092 \
    --group stocks-consumer-group \
    --reset-offsets --to-latest \
    --topic stocks-financials-raw --execute
```

Same pattern for weather. Both resets must be done before triggering the producers — not after.

---

## Issue 2 — Snowflake Writes Failing Silently in write_to_snowflake

### Symptom

After fixing the Kafka offset issue, the first end-to-end test had `consume_from_kafka` succeed (records received) but `write_to_snowflake` failed with:

```
[ERROR] Unexpected ProgrammingError: 251001: 251001: Account must be specified
```

### Root Cause

`snowflake_client.py` uses `SnowflakeHook` from `apache-airflow-providers-snowflake`. The installed version is **6.10.0**. In this version, `_get_conn_params()` reads the Snowflake account identifier from `extra_dejson["account"]` — not from the connection's `host` field.

The `AIRFLOW_CONN_SNOWFLAKE_DEFAULT` environment variable (injected via Kubernetes secret by `scripts/deploy/sync.sh`) was set using a URI format:

```
snowflake://PIPELINE_USER:pass@qztxwkd-lsc26305/PIPELINE_DB/RAW?warehouse=PIPELINE_WH&role=PIPELINE_ROLE
```

When Airflow parses this URI, `qztxwkd-lsc26305` lands in the `host` field. But the SnowflakeHook 6.x `_get_conn_params()` ignores `host` for account resolution — it only checks `extra_dejson["account"]`. Since that key was absent, `account` came back as an empty string, and every attempt to open a Snowflake connection failed.

This regression was silent because `write_to_snowflake` was never actually reached during the period when the Kafka consumer was returning 0 records. The task always short-circuited before calling any Snowflake code.

### Fix

Changed `scripts/deploy/sync.sh` to build the connection as a JSON object instead of a URI string. The JSON format places the account explicitly inside `extra`:

```json
{
  "conn_type": "snowflake",
  "login": "PIPELINE_USER",
  "password": "...",
  "extra": {
    "account": "qztxwkd-lsc26305",
    "database": "PIPELINE_DB",
    "schema": "RAW",
    "warehouse": "PIPELINE_WH",
    "role": "PIPELINE_ROLE"
  }
}
```

The Kubernetes secret in both namespaces (`airflow-my-namespace` and `default`) was patched immediately and the scheduler pod was restarted to pick up the change. Future deploys via `./scripts/deploy.sh` will produce the correct format automatically.

A verification step was added to `RESTORE_VERIFICATION.md` Step 3: after retrieving the connection, confirm that `extra_dejson` contains a non-empty `"account"` field.

---

## Issue 3 — Staleness Monitor Querying a Database That No Longer Exists

### Symptom

`Data_Staleness_Monitor` DAG was paused and stuck in `queued` state. After unpausing, the `run_staleness_check` task hit retry immediately with:

```
Database error during staleness check: (pymysql.err.OperationalError)
(2003, "Can't connect to MySQL server on '172.31.81.4' ([Errno 111] Connection refused)")
```

### Root Cause

`alerting/staleness.py` was hardcoded to call `make_mariadb_engine()` from `shared/db.py`, which connects to a MariaDB instance at the EC2 host's private IP (`172.31.81.4`). MariaDB is not installed or running — the project migrated from MariaDB to Snowflake as the primary data backend at some point after the staleness check was originally written. The dashboard pod confirmed this: `DB_BACKEND=snowflake` is set in its environment, so it queries Snowflake directly. The staleness check was simply never updated to match.

### Fix

Updated `alerting/staleness.py` to drop the MariaDB dependency entirely and use `SnowflakeHook(snowflake_conn_id="snowflake_default")` instead. The queries were updated to use fully-qualified MARTS table names:

- `company_financials` → `PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS`
- `weather_hourly` → `PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY`

`imported_at` in `FCT_WEATHER_HOURLY` is a `TIMESTAMP_NTZ` (Python `datetime`). The comparison with `datetime.now()` was updated to strip timezone info if present, since `datetime.now()` is naive.

The `except SQLAlchemyError` clause was broadened to `except Exception` since the SnowflakeHook uses its own exception hierarchy, not SQLAlchemy's.

After the fix, the staleness monitor runs cleanly and correctly reports:
- **Stocks**: always alerts because `FILED_DATE` is the SEC EDGAR filing date (e.g. Feb 2026 for FY2025 10-Ks), not a pipeline run timestamp. This is expected — the threshold of 168h was designed for operational freshness but the field doesn't reflect when we last ran the pipeline. Not a problem.
- **Weather**: resolves as fresh within 0.2h after a successful pipeline run.

---

## Other Doc Corrections in RESTORE_VERIFICATION.md

- **Step 1**: The Flask dashboard pod (`my-kuber-pod-flask`) lives in the `default` namespace, not `airflow-my-namespace`. The Airflow 3.x "webserver" pod is called `api-server`.
- **Step 14**: `airflow dags unpause` output always prints the previous `is_paused` state — the unpause did take effect even when the output shows `True`. Also documented the expected staleness alert behaviour for the stocks table.

---

## Files Changed

- `scripts/deploy/sync.sh` — Step 2c1a: switched `AIRFLOW_CONN_SNOWFLAKE_DEFAULT` from URI to JSON format
- `airflow/dags/alerting/staleness.py` — replaced `make_mariadb_engine()` with `SnowflakeHook`; updated table names and timestamp handling
- `docs/RESTORE_VERIFICATION.md` — Steps 1, 3, 8, 10, 14 updated with correct commands, pod locations, and expected alert behaviour

---

## How to Verify on Next Fresh Deploy

1. After deploy, run `airflow connections get snowflake_default` and confirm `extra_dejson` contains `"account": "qztxwkd-lsc26305"` (non-empty).
2. Before triggering any pipeline, run the `--to-latest` Kafka consumer group resets for both topics.
3. Both `stock_consumer_pipeline` and `weather_consumer_pipeline` should show all tasks `success` with no `skipped` tasks.
4. Staleness monitor should complete in under 15 seconds and report weather freshness correctly.
