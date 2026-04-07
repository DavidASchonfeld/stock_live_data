# Common Issues & Fixes (J-N)

Back to [Debugging Index](../DEBUGGING.md) | [Common Issues (A-I)](common-issues-1.md)

---

### J. Deploy script fails: `rsync: [Receiver] mkdir "/home/ubuntu/dashboard/manifests" failed`

**Symptoms:** Running `./scripts/deploy.sh` fails in Step 2c with: `rsync: [Receiver] mkdir "/home/ubuntu/dashboard/manifests" failed: No such file or directory (2)`

**Root Cause:** The deploy script's Step 1 creates directories on EC2 for Airflow DAGs and the dashboard build folder, but **did not create the dashboard manifests subdirectory**. The `mkdir -p` command was missing `$EC2_DASHBOARD_PATH/manifests`:

```bash
# BEFORE (line 21) — missing dashboard/manifests:
ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH"

# AFTER (fixed) — includes dashboard manifests:
ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests"
```

Later in Step 2c, the script rsyncs `dashboard/manifests/` to `/home/ubuntu/dashboard/manifests/`. Without the directory existing first, rsync fails.

**Solution Applied:**

1. **Added `EC2_DASHBOARD_PATH` variable** (line 12):
   ```bash
   EC2_DASHBOARD_PATH="/home/ubuntu/dashboard"
   ```

2. **Updated `mkdir` command** (line 21) to include the manifests subdirectory:
   ```bash
   ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests"
   ```

`mkdir -p` creates directories recursively and is idempotent (safe to re-run).

**Verify it's fixed:**
```bash
./scripts/deploy.sh
# Should complete all 7 steps without rsync errors in Step 2c
```

---

### K. Weather DAG load() task fails with database insert errors

**Symptoms:**
- `API_Weather-Pull_Data` DAG's `load()` task shows state `up_for_retry` or `failed`
- No clear error message in scheduler logs
- The `weather_hourly` table doesn't exist in MariaDB, causing SQLAlchemy to fail on insert

**Root Causes (layered):**

1. **Missing database table:** The `load()` task uses `pandas.DataFrame.to_sql()` with `if_exists="append"`, which should auto-create the table. However, if the table doesn't exist AND there are other issues (see below), the insert fails before the table is created.

2. **Data structure issue in transform → load pipeline:** While the transform task correctly creates a flattened DataFrame with proper columns (time, temperature_2m, latitude, etc.), the data being received by the load task showed incorrect structure — columns named after API response top-level keys (hourly, temperature_2m, latitude, etc.) instead of the flattened measurement rows.

3. **XCom serialization/deserialization:** XCom (Airflow's cross-task communication) serializes task outputs to JSON and deserializes them. If the transform task's `newDataFrame.to_dict(orient="records")` returns a list-of-dicts correctly, but the load task receives something different, the issue is in the XCom round-trip.

**Diagnosis Approach:**

```bash
# Step 1: Check if the table exists
kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- python3 -c "
from sqlalchemy import create_engine, text
engine = create_engine('mysql+pymysql://airflow_user:PASSWORD@<MARIADB_PRIVATE_IP>/database_one')
with engine.connect() as conn:
    result = conn.execute(text('SHOW TABLES LIKE \"weather_hourly\"'))
    print('Table exists:', bool(result.scalar()))
"

# Step 2: Check recent log files from OutputTextWriter to see what the load() task received
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  cat /opt/airflow/out/$(ls -t /opt/airflow/out | head -1) | tail -50

# Step 3: Check the full error in scheduler logs
kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=200 | grep -A 5 "load.*failed"
```

**Solution Applied:**

1. **Manually created the `weather_hourly` table** on EC2:
   ```bash
   kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- python3 -c "
   from sqlalchemy import create_engine, text
   engine = create_engine('mysql+pymysql://airflow_user:PASSWORD@<MARIADB_PRIVATE_IP>/database_one')
   create_table_sql = '''
   CREATE TABLE IF NOT EXISTS weather_hourly (
       id INT AUTO_INCREMENT PRIMARY KEY,
       time VARCHAR(50),
       temperature_2m FLOAT,
       latitude FLOAT,
       longitude FLOAT,
       elevation FLOAT,
       timezone VARCHAR(100),
       utc_offset_seconds INT,
       imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
   )
   '''
   with engine.connect() as conn:
       conn.execute(text(create_table_sql))
       conn.commit()
       print('Table created')
   "
   ```

**Limitations:** This is a workaround, not a root fix. The DataFrame structure mismatch (if real) could cause silent data corruption. After the load task succeeds, verify data quality:
```bash
kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- python3 -c "
from sqlalchemy import create_engine, text
engine = create_engine('mysql+pymysql://airflow_user:PASSWORD@<MARIADB_PRIVATE_IP>/database_one')
with engine.connect() as conn:
    result = conn.execute(text('SELECT * FROM weather_hourly LIMIT 1'))
    row = result.first()
    print('Columns:', list(result.keys()))
    print('Row 0:', row)
"
```

**Prevention for future DAGs:**

1. In the `load()` task, add explicit schema validation before inserting:
   ```python
   expected_columns = {"time", "temperature_2m", "latitude", "longitude", "elevation", "timezone", "utc_offset_seconds", "imported_at"}
   actual_columns = set(myDataFrameThing.columns)
   assert expected_columns == actual_columns, f"Schema mismatch: expected {expected_columns}, got {actual_columns}"
   ```

2. Consider creating tables with explicit schema in the `extract()` or `setup()` task rather than relying on pandas' auto-creation. This decouples table structure from DAG code.

---

### L. All Static Assets Fail — "Network Connection Was Lost" (Webserver OOMKill)

**Symptoms:** Airflow UI loads but has no styling or JavaScript. Browser DevTools shows 10+ simultaneous "network connection was lost" errors for CSS/JS files — all failing at once, not selectively.

**Key distinction:** A *single* API failure (e.g., the grid view) points to a DAG parse error (see Issue I). *All* files failing simultaneously points to a pod restart mid-connection.

**Diagnose:**
```bash
# Check if webserver was OOMKilled
kubectl describe pod -l component=webserver -n airflow-my-namespace | grep -A5 "Last State:"
# Look for: Reason: OOMKilled

# Check memory limit (should be 2Gi after the 2026-04-05 fix)
kubectl describe pod -l component=webserver -n airflow-my-namespace | grep -A3 "Limits:"

# Check live memory usage
kubectl top pod -n airflow-my-namespace
```

**Fix:**
1. Ensure `values.yaml` has `webserver.resources.limits.memory: 2Gi` and `webserver.env: AIRFLOW__WEBSERVER__WORKERS: "2"`
2. Run `./scripts/deploy.sh` — Step 2d applies the Helm values via `helm upgrade`
3. Verify workers: `kubectl exec -n airflow-my-namespace deploy/airflow-webserver -- env | grep WORKERS`

**Common mistake:** Using `airflow.config.AIRFLOW__WEBSERVER__WORKERS` in `values.yaml` — this key is silently ignored by the Helm chart. Pod environment variables for specific components must use `<component>.env`, e.g. `webserver.env`.

---

### M. 404 on DAG Run API Call — Can't Click Into Task Logs (Airflow 2.9.3 UI Bug)

**Symptoms:**
- Browser DevTools shows: `Failed to load resource: 404 (NOT FOUND)` for a URL like `...dagRuns/scheduled__2026-04-05T18:40:00%2000:00`
- Clicking a task in the grid view shows nothing or a blank panel
- The task run clearly executed (other tasks show success/failure), but you can't read the log through the UI

**Root Cause:**
The `+` in timezone-offset run IDs (e.g. `scheduled__2026-04-05T18:40:00+00:00`) is incorrectly encoded as `%20` (space) by the Airflow 2.9.3 frontend instead of `%2B`. The REST API then can't find a run with a space in its ID, returning 404.

This is a UI rendering bug — the task DID execute. Its state (failed/success) is correct; only the detail view is broken.

**Workaround — read task logs directly without the UI:**

```bash
# Option A: read the most recent PVC log file (written by OutputTextWriter)
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  sh -c 'cat /opt/airflow/out/$(ls -t /opt/airflow/out | head -1)'

# Option B: read Airflow's own task log from the log PVC
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  find /opt/airflow/logs/API_Weather-Pull_Data/load -type f | sort -r | head -3
# Then cat the most recent one

# Option C: tail scheduler logs and grep for the failing task
kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=200 | grep -E "\[ERROR\]|Connection failed|load.*failed"
```

**When this typically appears:**
- After an EC2 or MariaDB migration — the `DB_HOST` in the K8s secret may point to the old instance IP, causing `OperationalError` in `load()`. Use Option A or C above to confirm.
- After bootstrap on a new EC2 — the `weather_hourly` table doesn't exist yet, but `to_sql(if_exists="append")` auto-creates it on first successful connection.

**Permanent fix:**
None available through DAG code — this is an Airflow 2.9.3 frontend bug. Resolved in a later Airflow version. Workaround above is sufficient for development use.

---

### N. `load()` task fails with `ModuleNotFoundError: No module named 'pymysql'`

**Symptoms:**
- `extract()` and `transform()` succeed; `load()` fails immediately
- Task log shows:
  ```
  ModuleNotFoundError: No module named 'pymysql'
  File ".../dag_weather.py", line 197, in load
      engine = create_engine(f"mysql+pymysql://...")
  ```

**Root Cause:**
The DAGs use SQLAlchemy's `mysql+pymysql://` connection dialect, which requires the `pymysql` package as the underlying database driver. The Apache Airflow Docker image does not include `pymysql` by default. On a **fresh Helm deployment** (e.g. after an EC2 or Ubuntu migration), the pod starts with a clean Python environment and the package is missing.

This only surfaces in `load()` — not `extract()` or `transform()` — because those tasks never open a database connection.

**Why extract/transform succeed but load fails:**
```
extract()   → calls Open-Meteo API (HTTP) — no pymysql needed
transform() → pure Python/pandas data reshaping — no pymysql needed
load()      → create_engine("mysql+pymysql://...") → imports pymysql → ModuleNotFoundError
```

**Fix — add `_PIP_ADDITIONAL_REQUIREMENTS` to `values.yaml`:**
```yaml
# Install pymysql so SQLAlchemy can connect to MariaDB via mysql+pymysql://
env:
  - name: _PIP_ADDITIONAL_REQUIREMENTS
    value: "pymysql"
```

**How the fix works:**
The Airflow Helm chart runs `pip install <value>` during each pod's init phase when this env var is set, ensuring every pod type gets the package.

**Deploy the fix:**
```bash
# From your Mac:
./scripts/deploy.sh
# Step 2d (helm upgrade) applies the values.yaml change; pods restart and install pymysql
```

**Verify it's installed after pods restart:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  python3 -c "import pymysql; print('pymysql', pymysql.__version__)"
```

**When to expect this again:** Any time the Airflow Helm release is fully uninstalled and reinstalled (e.g. `helm delete` + `helm install`). A `helm upgrade` preserves the existing `values.yaml` so the package stays installed.
