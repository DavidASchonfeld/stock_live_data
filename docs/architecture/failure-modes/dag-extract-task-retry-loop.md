# Failure Mode: DAG Extract Task Retry Loop

**Date:** 2026-04-11
**DAGs affected:** `API_Weather-Pull_Data`, `Stock_Market_Pipeline` (and by extension their consumer DAGs)
**Symptom:** Extract tasks hang for ~88 seconds, fail, then retry and hang again

---

## What happened

Both producer DAGs (`API_Weather-Pull_Data` and `Stock_Market_Pipeline`) were stuck in a retry loop. The `extract` task would start, run for about 1 minute 28 seconds, then fail. Because `retries: 1` is set in `default_args`, Airflow would automatically retry the task 5 minutes later — and the same hang would happen again.

The Airflow logs showed the task reaching the API call but never completing:

```
[2026-04-11 21:38:27] INFO - Filling up the DagBag from /opt/airflow/dags/dag_weather.py
[2026-04-11 21:38:29] INFO - Stats instance was created in PID 7 but accessed in PID 127. Re-initializing.
```

The "Stats instance re-initializing" lines are a side-effect: each time a task is spawned and killed, a new Python process is created, causing Airflow's StatsD client to reinitialize. This is not the root cause — it's a symptom of the process churn caused by hanging tasks.

---

## Root causes

### 1. Missing HTTP timeout in `weather_client.py` (PRIMARY)

`fetch_weather_forecast()` called `requests.get()` with no `timeout=` argument:

```python
# Before (broken)
response = requests.get(base_url, params=parameters)
```

Python's `requests` library will wait forever if the remote server accepts the TCP connection but never sends a response. When Open-Meteo was slow or unreachable, the task blocked until the OS-level TCP keepalive fired — roughly 88 seconds on this system.

### 2. No `execution_timeout` on any task (SECONDARY)

None of the 4 DAGs set `execution_timeout` in `default_args`. Without this, Airflow has no upper bound on how long a task can run. A single hanging task can block a LocalExecutor worker slot indefinitely — or until an external timeout (OS, Kubernetes liveness probe) kills it.

### 3. No `max_block_ms` on `KafkaProducer` (TERTIARY)

Both producer DAGs created `KafkaProducer` without specifying `max_block_ms`. The default is 60 seconds. If the Kafka broker is unreachable, `producer.send()` or `producer.flush()` will silently block for up to 60 seconds before raising an error. Same hang-then-fail pattern as the HTTP issue.

---

## Why stocks DAG was also affected

The `Stock_Market_Pipeline` `extract` task calls `edgar_client.py`, which does have `timeout=30` on its HTTP calls. However, stocks still shared the missing `execution_timeout` problem — meaning if edgar_client.py's own timeout was ever bypassed, the task would hang indefinitely.

---

## Fix

Three changes applied across 5 files:

| File | Change |
|------|--------|
| `airflow/dags/weather_client.py` | Added `timeout=10` to `requests.get()` |
| `airflow/dags/dag_weather.py` | Added `execution_timeout=timedelta(minutes=10)` to `default_args`; added `max_block_ms=15000` to `KafkaProducer` |
| `airflow/dags/dag_stocks.py` | Added `execution_timeout=timedelta(minutes=10)` to `default_args`; added `max_block_ms=15000` to `KafkaProducer` |
| `airflow/dags/dag_weather_consumer.py` | Added `execution_timeout=timedelta(minutes=20)` to `default_args` |
| `airflow/dags/dag_stocks_consumer.py` | Added `execution_timeout=timedelta(minutes=20)` to `default_args` |

The 10-minute ceiling for producer DAGs covers: extract (~3–5 min), transform (~2–3 min), and publish (~1 min). The 20-minute ceiling for consumer DAGs covers: consume (30s), Snowflake write (~2 min), dbt_run (~15 min), and anomaly detection (~5 min).

---

## How to detect this in the future

- Task `Duration` in the Airflow UI is suspiciously long (>60s for a simple API call)
- Task is on `Try Number 2` with no clear error in the log — the log just stops mid-flight
- `Stats instance was created in PID X but accessed in PID Y` messages repeat across multiple task instances
- Once `execution_timeout` is in place, future hangs will surface as `AirflowTaskTimeout` with a clear traceback instead of a silent OS kill
