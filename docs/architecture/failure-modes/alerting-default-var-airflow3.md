# Incident: DAGs Stuck in Retry Loop — alerting.py Airflow 3.x Incompatibility

**Date:** April 11, 2026
**DAGs affected:** `Stock_Market_Pipeline`, `API_Weather-Pull_Data`
**Symptom:** Both DAGs stuck in retry loop; task logs showed attempt=14 with no useful error output beyond DAG loading messages.

---

## What Happened

Both pipelines were retrying indefinitely and never completing. The task logs only showed Airflow's DagBag initialization messages — no actual task output, no exception traceback. This is characteristic of a crash happening in the Airflow callback layer rather than in the task code itself.

Every task in both DAGs has `on_failure_callback` and `on_retry_callback` set to functions in `alerting.py`. Whenever a task failed for any reason, Airflow called `on_failure_alert()`. That function immediately crashed with:

```
TypeError: get() got an unexpected keyword argument 'default_var'
```

This left tasks in a corrupted retry state, producing the runaway retry loop.

---

## Root Cause

`alerting.py` was using the old Airflow 2.x import and parameter name:

```python
# Before (broken in Airflow 3.x)
from airflow.models import Variable
url = Variable.get("SLACK_WEBHOOK_URL", default_var=None)
```

Airflow 3.x renamed both the recommended import path and the parameter:
- `airflow.models.Variable` → `airflow.sdk.Variable`
- `default_var=` → `default=`

`dag_utils.py` (which uses the same pattern) was already updated to Airflow 3.x syntax, but `alerting.py` was missed during the migration.

This is the same class of issue as the April 8 incident ("missing alerting.py module"), where a broken callback caused DAGs to get stuck in "Up for Retry". The fix then was restoring the missing file; the fix now is correcting the API call inside it.

---

## Fix Applied

**`airflow/dags/alerting.py`:**

1. Changed `from airflow.models import Variable` → `from airflow.sdk import Variable`
2. Changed all three `Variable.get("SLACK_WEBHOOK_URL", default_var=None)` calls to `default=None`

**Also deployed in same release:**
- Deferred `import pandas as pd` inside task functions in all four DAG files and `weather_client.py` (prevents slow DagBag parsing from holding up task workers)
- `airflow/helm/values.yaml`: scheduler CPU raised 100m→200m with offsetting reductions to webserver/triggerer/dagProcessor; probe timeouts raised 45→60s; `AIRFLOW_VAR_KAFKA_BOOTSTRAP_SERVERS` added as env var to eliminate runtime Variable lookup for Kafka config

---

## How to Detect This in the Future

- Task logs that end immediately after "Filling up the DagBag" with no task output indicate a crash in the pre-task setup layer (DagBag load or callback infrastructure).
- Search task logs for `TypeError` in the callback — Airflow logs callback exceptions separately from task output.
- Any time Airflow is upgraded, search `alerting.py` and `dag_utils.py` for deprecated parameter names (`default_var`, `provide_context`, etc.).

---

## Prevention

When Airflow is upgraded, check the changelog for `Variable.get()` and all callback-related APIs. `dag_utils.py` and `alerting.py` should always use the same import path (`airflow.sdk`) and parameter names so the pattern stays consistent and obvious.
