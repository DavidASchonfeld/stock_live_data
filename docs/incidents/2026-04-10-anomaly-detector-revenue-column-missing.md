# Incident: anomaly_detector.py — KeyError 'revenue' + urllib3 Version Warning

**Date:** 2026-04-10
**Severity:** Blocking (anomaly detection task fails entirely)

---

## Errors

```
KeyError: "Columns not found: 'revenue'"
  File "/opt/airflow/dags/anomaly_detector.py", line 67, in fetch_data
    wide.groupby("ticker")[["revenue", "net_income"]].pct_change()
```

```
RequestsDependencyWarning: urllib3 (2.6.0.post1) or chardet (5.2.0)/charset_normalizer (3.3.2)
doesn't match a supported version!
```

---

## How It Was Encountered

Verification step 7 — dry-running `anomaly_detector.py` directly inside the scheduler pod:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

The script exited with a traceback. The urllib3 warning was visible on every `airflow` CLI command output (steps 5–6).

---

## Root Cause

### KeyError: 'revenue'

`anomaly_detector.py` queried Snowflake with:

```sql
WHERE UPPER(metric) IN ('REVENUES', 'NETINCOMELOSS')
```

But `edgar_client.py` (the producer) stores revenue under the XBRL concept name `RevenueFromContractWithCustomerExcludingAssessedTax` — not `Revenues`. `UPPER('RevenueFromContractWithCustomerExcludingAssessedTax')` is `REVENUEFROMCONTRACTWITHCUSTOMEREXCLUDINGASSESSEDTAX`, which never matches `'REVENUES'`. The query returned zero revenue rows.

With no revenue rows, `pivot_table()` produced no `revenues` column. The rename `"revenues": "revenue"` was a no-op. When line 67 tried to select `["revenue", "net_income"]` from the grouped DataFrame, `revenue` didn't exist — hence the `KeyError`.

`NetIncomeLoss` was unaffected because its XBRL concept name matches `NETINCOMELOSS` exactly.

### urllib3 Warning

The Airflow base image ships with a `requests` version that validates urllib3/chardet against a hardcoded compatible range. Newer transitive dependencies (urllib3 2.x, chardet 6.x) fall outside that range, triggering the warning. The fix (`pip install "requests>=2.32.0"`) was already written into `airflow/docker/Dockerfile` but the image hadn't been rebuilt and deployed yet.

---

## How It Was Identified

1. **urllib3 warning:** warning text names the exact package and version. Tracing the path (`/home/airflow/.local/lib/python3.12/site-packages/requests/__init__.py`) confirmed it was Airflow's Python environment. The Dockerfile already had the fix, confirming it was a deploy-lag issue only.

2. **KeyError:** traceback pointed to line 67 — the `groupby` column selector. Working backward: the column would only be missing if the rename at line 58 did nothing, which would only happen if the pivot produced no `revenues` column, which would only happen if the SQL returned no revenue rows. Checking `edgar_client.py:44` revealed the actual XBRL concept name (`RevenueFromContractWithCustomerExcludingAssessedTax`) used to populate Snowflake — it didn't match the filter string `'REVENUES'` at all.

---

## Fix

**`airflow/dags/anomaly_detector.py`** — two lines:

```diff
- WHERE UPPER(metric) IN ('REVENUES', 'NETINCOMELOSS')
+ WHERE UPPER(metric) IN ('REVENUEFROMCONTRACTWITHCUSTOMEREXCLUDINGASSESSEDTAX', 'NETINCOMELOSS')  -- matches XBRL concept fetched by edgar_client.py
```

```diff
- "revenues": "revenue",          # lowercased by str.lower() above — was "Revenues"
+ "revenuefromcontractwithcustomerexcludingassessedtax": "revenue",  # XBRL name from edgar_client.py, lowercased
```

The urllib3 fix was already present in the Dockerfile — no additional code change needed, only a redeploy.

---

## Why This Fix

The SQL filter had to match what is actually in Snowflake. `edgar_client.py` is the single source of truth for which XBRL concepts are fetched and written; aligning the anomaly detector query to that source is the correct and minimal fix. No other revenue concepts are fetched by the pipeline, so no broader normalization was needed.

---

## How the Fix Solved the Problem

The corrected filter returns revenue rows from Snowflake. `pivot_table()` now produces a `revenuefromcontractwithcustomerexcludingassessedtax` column, the rename maps it to `revenue`, and line 67's `groupby()[["revenue", "net_income"]]` resolves successfully. The `requests` upgrade in the Dockerfile eliminates the urllib3 version check that was emitting the warning, once deployed.
