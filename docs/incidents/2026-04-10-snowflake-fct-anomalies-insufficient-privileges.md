# Incident: FCT_ANOMALIES — Insufficient Privileges

**Date:** 2026-04-10  
**Severity:** Low (pipeline writes succeeded; only ad-hoc SELECT was blocked)  
**Status:** Second error found during verification — fix applied, pending redeploy + re-verify

---

## What Happened

During Step 9 of the ML verification checklist, querying `FCT_ANOMALIES` in the Snowflake UI returned:

```
SQL access control error: Insufficient privileges to operate on table 'FCT_ANOMALIES'
```

Steps 5–8 had all passed — the DAG ran, the anomaly detector wrote results, and MLflow logged the run. The table existed but could not be read.

---

## Root Cause

`anomaly_detector.py` opens a Snowflake connection without specifying a `role`:

```python
snowflake.connector.connect(
    account=..., user="PIPELINE_USER", password=..., ...
    # no role= specified
)
```

When no role is given, Snowflake falls back to `PIPELINE_USER`'s **default role** (in this case `PUBLIC` or a minimal custom role). That role became the **owner** of the `ANALYTICS` schema and `FCT_ANOMALIES` table when the script first created them.

The ad-hoc query in the Snowflake UI was run under `SYSADMIN` (the typical UI default). Because `SYSADMIN` was never granted access to objects owned by `PIPELINE_USER`'s default role, the SELECT was rejected.

---

## How It Was Identified

- The pipeline DAG and `anomaly_detector.py` both ran without errors (writes succeeded).
- The Snowflake UI `SELECT COUNT(*)` returned the privileges error.
- Inspecting `anomaly_detector.py:get_snowflake_conn()` confirmed no `role=` parameter was set.

---

## Fix — Original (Phase 1)

Added `role=os.environ.get("SNOWFLAKE_ROLE", "SYSADMIN")` to `get_snowflake_conn()` and planned to add `SNOWFLAKE_ROLE: "SYSADMIN"` to the `snowflake-credentials` K8s secret.

---

## Second Error — Found During Verification (2026-04-10)

`airflow tasks test stock_consumer_pipeline detect_anomalies` failed with:

```
snowflake.connector.errors.DatabaseError: 250001 (08001): Role 'SYSADMIN' specified in the
connect string is not granted to this user, or is not permitted for the credentials being used.
```

**Root cause:** `SNOWFLAKE_ROLE` was never added to the K8s secret, so the code fell back to the `"SYSADMIN"` default — but `SYSADMIN` is not granted to the pipeline Snowflake user. The pipeline user's actual granted role is `PIPELINE_ROLE`.

### Fix — Phase 2 (two-part)

**1. Code (`anomaly_detector.py:25`) — changed default to PIPELINE_ROLE:**

```python
role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),
```

**2. Config — `SNOWFLAKE_ROLE` baked into `deploy.sh` (step 2c1a):**

Added step 2c1a to `scripts/deploy.sh` that patches `SNOWFLAKE_ROLE=PIPELINE_ROLE` into the `snowflake-credentials` secret in both namespaces on every deploy — no manual EC2 edits needed.

Run `./scripts/deploy.sh` (step 2c1a patches the secret; step 2d restarts pods to pick it up).

---

## Why This Fix Works

Snowflake RBAC is ownership-based — the role that creates an object owns it. Pinning the pipeline to `PIPELINE_ROLE` (the role actually granted to the Snowflake user) ensures objects are created under the correct owner and the connection succeeds.

---

## Verification

> **Deploy is already complete** — `./scripts/deploy.sh` has been run.

**1. Trigger the DAG**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags trigger stock_consumer_pipeline
```

**2. Check task states for the new run**

Copy the full `dag_run_id` printed by the trigger command and **quote it** — the string
contains `:`, `.`, and `+` which bash interprets as special characters without quotes.
Using `<run_id>` literally (without quotes) causes a bash syntax error because `<` is a
redirect operator.

```bash
# Quote the full run_id; do NOT truncate it
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks states-for-dag-run stock_consumer_pipeline \
    "manual__2026-04-10T23:05:10.813949+00:00"
```

Re-run every ~30s until all tasks show `success` or `skipped`.

**3. If `detect_anomalies` shows `skipped`** (ShortCircuitOperator fired because no new Kafka
data was available), use `tasks test` to run it directly — Airflow 3.x removed `tasks run`;
`tasks test` executes the task without writing state to the metadata DB, which is fine for verification:

```bash
# tasks test executes the callable directly, bypassing DAG dependencies
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow tasks test stock_consumer_pipeline detect_anomalies
```

**4. Task output appears inline** when using `tasks test` — no separate log fetch needed.

What to look for:
- No `Insufficient privileges` or `access control` errors
- MLflow tracking URL line present
- Final JSON line: `{"n_anomalies": ..., "n_total": ..., "mlflow_run_id": "..."}`

**5. Confirm the row landed in Snowflake**
```sql
SELECT COUNT(*) FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES;
-- Expect: integer ≥ 1
```
