# Incident: DAGs OOM-Killed During DagBag Loading (Apr 12, 2026)

## Symptom

`API_Weather-Pull_Data` and `Stock_Market_Pipeline` DAGs repeatedly failed on every attempt.
Task logs ended abruptly with no Python traceback — just DAG loading messages, then silence:

```
[2026-04-11 22:04:47] INFO - DAG bundles loaded: dags-folder
[2026-04-11 22:04:47] INFO - Filling up the DagBag from /opt/airflow/dags/dag_weather.py
[2026-04-11 22:04:49] INFO - Stats instance was created in PID 8 but accessed in PID 136. Re-initializing.
[2026-04-11 22:04:50] INFO - Stats instance was created in PID 136 but accessed in PID 141. Re-initializing.
```

After this, the process restarted with no error. Both DAGs were stuck in a permanent retry loop.

## Root Cause

**OOM Kill (SIGKILL) during DagBag loading.**

In Airflow 3.x, each task runs in a completely fresh subprocess that must reimport the full
Airflow SDK and all DAG dependencies from scratch. SIGKILL leaves no Python traceback — the
kernel kills the process before any error can be flushed to the log file.

The triggering regression was the introduction of `alerting/__init__.py` as part of an alerting
refactor. The new `__init__.py` eagerly re-exported `check_data_staleness` from `alerting/staleness.py`:

```python
from alerting.staleness import check_data_staleness  # line 16 — this was the problem
```

`staleness.py` imports `sqlalchemy` and `shared.db` at module level. This meant that every task
worker for every DAG that imports from `alerting` (weather, stocks, their consumers) now loaded
`sqlalchemy` at parse time — memory that was never needed for those DAGs.

The old `alerting.py` only needed `requests` + `airflow.sdk`. The refactored package silently
added sqlalchemy to the parse-time footprint of all four producer/consumer DAGs, tipping the
combined scheduler pod memory over the OOM threshold.

## Key Diagnostic Rule

> No Python traceback in a task log = OOM Kill.
> Python exceptions always produce tracebacks. SIGKILL does not.

## Fix

Three changes were applied:

1. **`alerting/__init__.py`** — Removed the `check_data_staleness` re-export. `staleness.py`
   is now only imported by `dag_staleness_check.py`, which imports it directly.

2. **`dag_staleness_check.py`** — Updated import to:
   ```python
   from alerting.staleness import check_data_staleness
   ```

3. **`airflow/helm/values.yaml`** — Added `python-dotenv` to `_PIP_ADDITIONAL_REQUIREMENTS`
   (defensive: `shared/config.py` imports it at module level and previously relied on it being
   a transitive Airflow dependency).

4. **`alerting.py`** (the old module file) — Deleted. Python always picks the `alerting/`
   package over the module file, so this file was dead code and misleading.

## Prevention

When refactoring a shared module into a package, audit what the new `__init__.py` re-exports.
Only re-export symbols that ALL callers need. Symbols used by a single DAG should be imported
directly from the submodule, not promoted to the package root — especially if those submodules
carry heavy dependencies (sqlalchemy, pandas, etc.) that would otherwise be skipped at parse time.
