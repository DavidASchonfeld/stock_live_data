# DAG Extract OOM Kill — April 12, 2026

## Summary

Both the Stock Market Pipeline and Weather Pipeline DAGs began silently failing at their `extract` task after the MLflow/dbt/OpenLineage integration was deployed. Tasks produced no Python exception — only two DagBag-load log lines appeared before the process was killed.

---

## Symptoms

- Both `Stock_Market_Pipeline` and `API_Weather-Pull_Data` failing on the `extract` task simultaneously
- No Python traceback — log ends abruptly after DagBag load messages:
  ```
  INFO - DAG bundles loaded: dags-folder
  INFO - Filling up the DagBag from /opt/airflow/dags/dag_weather.py
  INFO - Stats instance was created in PID 7 but accessed in PID 96. Re-initializing.
  INFO - Stats instance was created in PID 96 but accessed in PID 106. Re-initializing.
  [silence — process killed]
  ```
- Task duration ~1:52 before death (time for the scheduler pod to exhaust its memory ceiling under load)
- Both DAGs failing at the same time — hallmark of a **pod-level** OOM kill, not a task-level crash

---

## Root Cause

**The OpenLineage integration (MLflow Thread 1) doubled the per-task memory cost in the scheduler pod.**

Airflow 3.x with LocalExecutor runs all task workers inside the scheduler pod. When OpenLineage is enabled via `AIRFLOW__OPENLINEAGE__TRANSPORT`, Airflow spawns a **second subprocess after every task** to emit lineage events. This OL subprocess must load the full Airflow provider stack (~300MB), the same cost as the task worker itself.

Memory breakdown with two DAGs running concurrently (scheduler pod, 2Gi limit):

| Process | Memory |
|---|---|
| Scheduler process baseline | ~500MB |
| DAG processor subprocess | ~300MB |
| Weather `extract` task worker | ~400MB |
| Weather OpenLineage emitter subprocess | ~300MB |
| Stocks `extract` task worker | ~400MB |
| Stocks OpenLineage emitter subprocess | ~300MB |
| **Total** | **~2.2GB → over 2Gi ceiling** |

The pod was OOM-killed by Kubernetes, taking all running task workers with it. Because both DAGs happened to be running extract tasks at the same time (weather runs hourly; stocks runs daily; both were triggered close together), both died in the same kill event.

---

## Fix

**1. Increased scheduler memory limit from 2Gi to 3Gi** (`airflow/helm/values.yaml`)

This gives enough headroom for two concurrent DAGs with OpenLineage enabled. At ~2.2GB peak usage, 3Gi provides ~800MB of safety margin.

**2. Deferred SQLAlchemy import in `dag_staleness_check.py`**

`dag_staleness_check.py` was importing `from alerting.staleness import check_data_staleness` at module level. `alerting/staleness.py` imports SQLAlchemy at module level, so every DAG parse cycle caused the DAG processor (512Mi limit) to load SQLAlchemy unnecessarily. Moved the import inside the task function body so SQLAlchemy only loads when the staleness check task actually runs.

---

## Pattern to Watch For

**Both DAGs failing simultaneously with no Python exception = pod-level OOM kill.**

Any time a new Airflow integration adds subprocesses per task (OpenLineage, custom listeners, callbacks that spawn processes), multiply the expected memory cost by the number of concurrent tasks. On this single-node K3S setup the scheduler pod holds everything — there is no separate worker fleet to absorb the load.

---

## Prevention

- Before enabling any Airflow provider that spawns subprocesses, estimate the peak memory: `(baseline) + N_concurrent_tasks × (task_worker + provider_subprocess)` and confirm it fits within the scheduler limit.
- After major integrations (MLflow, OpenLineage, new providers), run both DAGs simultaneously and watch `kubectl top pod -n airflow-my-namespace` to confirm peak usage is under the limit.
