# Airflow DAG Issues — Discovery and Parse Errors

Troubleshooting DAG discovery failures, DagBag errors, parse-time import failures, and deprecation warnings.

**See also:** [DAG Runtime Issues](airflow-dag-runtime-issues.md) | [Parent index](../TROUBLESHOOTING.md) | [DEBUGGING.md](../DEBUGGING.md)

---

## Issue: Deprecation Warnings in Scheduler / DAG-Processor Logs After Airflow 3.x Upgrade

### Symptoms
- Scheduler or dag-processor logs show repeated lines like:
  ```
  DeprecationWarning: airflow.decorators.dag is deprecated. Use airflow.sdk.dag instead.
  DeprecationWarning: Using Variable.get from airflow.models is deprecated. Use airflow.sdk.Variable instead.
  ```
- Warnings appear on every DAG parse cycle (every ~5–30 seconds)
- DAGs still run correctly — these are warnings, not errors

### Root Cause
In Airflow 3.x, the public DAG-authoring API was consolidated into `airflow.sdk`. The legacy import paths (`airflow.decorators`, `airflow.models.Variable`, `airflow.models.xcom_arg.XComArg`) still work as compatibility shims but emit deprecation warnings on every parse.

**Exception:** `airflow.exceptions.AirflowSkipException` does NOT need changing — it stays in `airflow.exceptions` in 3.x.

### Fix
Update each affected file:

| File | Change |
|------|--------|
| `dag_stocks.py` | `from airflow.sdk import dag, task, XComArg` |
| `dag_weather.py` | `from airflow.sdk import dag, task, XComArg` |
| `dag_staleness_check.py` | `from airflow.sdk import dag, task` |
| `dag_utils.py` | `from airflow.sdk import Variable` |
| `alerting.py` (5 local imports) | `from airflow.sdk import Variable` |

Then deploy: `./scripts/deploy.sh`

### Verification
```bash
# After deploy, watch dag-processor logs — should produce no output
ssh ec2-stock "kubectl logs -n airflow-my-namespace -l component=dag-processor --tail=200 | grep -i deprecat"

# Confirm all DAGs still appear
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list"
```

---

## Issue: "DAG seems to be missing from DagBag" in Airflow UI

### Symptoms
- You can see a DAG in the Airflow UI list (e.g. `API_Weather-Pull_Data`)
- Clicking it shows: `DAG "API_Weather-Pull_Data" seems to be missing from DagBag`
- Other DAGs work fine

### Why This Error Exists — DagBag vs Metadata DB

Airflow stores DAG information in two separate places:

| Store | What it holds | When it's updated |
|-------|--------------|-------------------|
| **Metadata DB** (PostgreSQL) | DAG IDs, run history, task states | Updated after every *successful* parse |
| **DagBag** (in-memory) | Live DAG objects built by importing `.py` files | Rebuilt continuously by scheduler/dag-processor |

The **DAG list page** reads from the metadata DB — so a DAG that previously parsed successfully continues to show up in the list, even after its file starts failing. But when you **click a DAG**, the UI needs the live DagBag object to render the task graph, next run time, and run history. If the file is currently failing to parse, the DagBag has no entry for that dag_id, and you get "seems to be missing."

### Root Cause: Parse-Time Import Failures

Airflow "parses" a DAG file by importing it as a Python module. Every top-level statement runs immediately at import time. If any `import` fails, the entire file import fails, and Airflow has nothing to register in the DagBag.

**The specific failure in this project** (April 2026):

`dag_weather.py` imports `weather_client` at the top level. `weather_client.py` imported `api_key` at its top level — a gitignored file. After a pod recreation, `api_key.py` was not present, causing:

```
ModuleNotFoundError: No module named 'api_key'
  → weather_client.py fails to import
    → dag_weather.py fails to import
      → API_Weather-Pull_Data missing from DagBag
```

### What "Parse Time" vs "Execution Time" Means

```python
# ✗ TOP-LEVEL IMPORT — runs at parse time, breaks the whole DAG file if it fails
from some_optional_package import something

@dag(...)
def my_pipeline():
    @task
    def load():
        # ✓ LAZY IMPORT — runs only when the task executes, failure is contained
        from some_optional_package import something
        ...
    load()

dag = my_pipeline()
```

`@task` functions are wrapped by the decorator and only execute when Airflow runs the task. A lazy import inside a `@task` body is safe at parse time.

### Diagnosis

```bash
# 1. Check what the scheduler sees when it tries to import the file
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  python3 -c 'import sys; sys.path.insert(0, \"/opt/airflow/dags\"); import dag_weather' 2>&1"

# 2. Scan dag-processor logs for import errors
ssh ec2-stock "kubectl logs -n airflow-my-namespace -l component=dag-processor --tail=100 | grep -i 'error\|traceback\|weather'"

# 3. Confirm which DAGs are currently in the DagBag
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list 2>/dev/null"
```

### Fix Pattern

1. **Find the failing import** — look for `ModuleNotFoundError` or `ImportError` in the traceback
2. **Remove or lazy-ify the import**: if unused, delete it; if optional, move inside the function body
3. **Redeploy**: `./scripts/deploy.sh`
4. **Force reserialize** if the UI still shows the error:
   ```bash
   ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags reserialize"
   ```

### Prevention

- Never import gitignored files (`api_key.py`, `constants.py`, `db_config.py`) in library modules that other DAGs import
- Any package that isn't guaranteed to be installed (Snowflake, Kafka, etc.) should be imported lazily inside the function that uses it

---

## Issue: DAG File Exists but Not Discoverable by Airflow

### Symptoms
- DAG file exists in `/opt/airflow/dags/` (can verify with `ls`)
- But DAG doesn't appear in `airflow dags list`
- No import errors in scheduler logs

### Root Cause
The `@dag` decorator returns a DAG object when called. This object must be assigned to a **module-level variable** for Airflow's DAG parser to discover it.

```python
# ✗ WRONG - DAG not discoverable
@dag(dag_id="My_DAG")
def my_dag_function():
    ...
my_dag_function()  # Called but return value discarded

# ✓ CORRECT - DAG discoverable
@dag(dag_id="My_DAG")
def my_dag_function():
    ...
dag = my_dag_function()  # Assigned to module variable
```

### Solution

1. **Check your DAG file** — should end with a variable assignment like `dag = stock_market_pipeline()`
2. **If missing**, add it
3. **Deploy**: `./scripts/deploy.sh`
4. **Force reload**:
   ```bash
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags reserialize
   ```
5. **Verify**:
   ```bash
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags list | grep "Stock_Market_Pipeline"
   ```

---

## Issue: `Variable.get() got an unexpected keyword argument 'default_var'`

### Symptoms
- Task fails immediately with:
  ```
  TypeError: Variable.get() got an unexpected keyword argument 'default_var'
  ```
- Affects any task that calls `check_vacation_mode()` or the alerting cooldown helpers

### Root Cause
In Airflow 3.x (`airflow.sdk`), `Variable.get()` renamed `default_var` to `default`. The old kwarg no longer exists.

### Fix
Replace `default_var=` with `default=` everywhere `Variable.get()` is called:
```python
# Before (Airflow 2.x)
Variable.get("VACATION_MODE", default_var="false")

# After (Airflow 3.x)
Variable.get("VACATION_MODE", default="false")
```
Affected files: `dag_utils.py` and `alerting.py`.
