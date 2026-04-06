# Troubleshooting Guide

**Quick Navigation**
- Looking for general debugging approach? See [DEBUGGING.md](DEBUGGING.md)
- Need command explanations? See [../reference/COMMANDS.md](../reference/COMMANDS.md)
- Want to understand Airflow or ETL? See [../architecture/SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md)
- Looking for term definitions? See [../reference/GLOSSARY.md](../reference/GLOSSARY.md)
- Failure mode catalog? See [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md)
- Prevention checklists? See [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md)

---

## Issue: `apt upgrade -y` Appears Frozen / No Output for Several Minutes

### Symptoms
- `sudo apt upgrade -y` runs for a while then goes completely silent
- No output, no progress indicator, cursor just sits there
- Can happen mid-upgrade or at the start of a large package

### Root Cause
`apt upgrade` encountered a config file prompt for a package with locally-modified config files (e.g. `/etc/ssh/sshd_config`, `/etc/systemd/...`). The `-y` flag auto-confirms package installation but does **not** auto-answer config file diff prompts — those require explicit input.

### Fix
Press **Enter** to accept the default (keep the existing config file). The upgrade will resume immediately.

If it's still frozen after pressing Enter, try pressing `n` (keep current) or `y` (use new version) depending on the prompt context.

### Prevention (for scripts)
Use `DEBIAN_FRONTEND=noninteractive` to suppress all interactive prompts and always keep the current config:
```bash
sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y
```
This is safe for automated/scripted use but not recommended interactively — you won't see what config choices were made.

### Notes
- This is harmless — the upgrade did not fail, it was just waiting
- The prompt appears in the terminal output if you're actively watching, but is easy to miss in an overnight run
- Real incident: 2026-04-06, `apt upgrade` waited ~6 hours overnight for an Enter keypress ([CHANGELOG.md](../incidents/CHANGELOG.md))

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

## Issue: Warnings in `./scripts/deploy.sh` output

### Symptoms (resolved in April 2026 — documented for reference)

Four warning categories appeared during deploy:

```
WARNING! Your credentials are stored unencrypted in '/home/ubuntu/.docker/config.json'.
DEPRECATED: The legacy builder is deprecated and will be removed in a future release.
RequestsDependencyWarning: urllib3 (2.6.3) or chardet .../charset_normalizer ... doesn't match a supported version!
RemovedInAirflow4Warning: The airflow.security.permissions module is deprecated
```

### Root Causes & Fixes Applied

| Warning | Root Cause | Fix |
|---------|-----------|-----|
| Unencrypted Docker credentials | `docker login` writes ECR tokens to `~/.docker/config.json` in plaintext | Switched to `amazon-ecr-credential-helper` — fetches tokens from IAM role on demand, nothing stored on disk |
| Legacy Docker builder | Default `docker build` uses the old build engine | Added `DOCKER_BUILDKIT=1` env var to the docker build command in Step 4 |
| `RequestsDependencyWarning` | Older `requests` version didn't declare support for `urllib3 2.x` | Pinned `requests>=2.32.3` in `_PIP_ADDITIONAL_REQUIREMENTS` in `values.yaml` |
| `RemovedInAirflow4Warning` | `apache-airflow-providers-common-compat` (Airflow's own compat shim) was calling the deprecated `airflow.security.permissions` module | Upgraded `apache-airflow-providers-common-compat>=1.5.0` in `_PIP_ADDITIONAL_REQUIREMENTS` |

### Notes
- The `requests` and `providers-common-compat` warnings came from **Airflow's own provider packages**, not our DAG code — DAG files were fully audited and are clean
- The ECR credential helper (`amazon-ecr-credential-helper`) is the [AWS-recommended approach](https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html) for ECR authentication; it uses the EC2 instance's IAM role and requires no stored credentials
- `_PIP_ADDITIONAL_REQUIREMENTS` installs packages at every pod startup — this adds ~15–30s to restart time but is appropriate for this project; a custom Airflow image would be faster for high-churn environments

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

This split is why one DAG can disappear from detail view while others remain fine — it's not about the file being deleted, it's about the file failing to import.

### Root Cause: Parse-Time Import Failures

Airflow "parses" a DAG file by importing it as a Python module. Every top-level statement in the file runs immediately at import time — including all `import` and `from x import y` lines. If any of those fail (package not installed, file not found), Python raises an exception, the entire file import fails, and Airflow has nothing to register in the DagBag.

**The specific failure in this project** (April 2026):

`dag_weather.py` imports `weather_client` at the top level. `weather_client.py` imported `api_key` at its top level — a gitignored file containing API keys that was a leftover from when the file handled OpenWeatherMap. Open-Meteo (the current API) is free and keyless; `api_keys` was never referenced in the function body. After a pod recreation (Ubuntu 24.04 migration), `api_key.py` may not have been present on the pod, causing:

```
ModuleNotFoundError: No module named 'api_key'
  → weather_client.py fails to import
    → dag_weather.py fails to import
      → API_Weather-Pull_Data missing from DagBag
```

The Stocks DAG was unaffected because `stock_client.py` has no dependency on `api_key.py`.

### What "Parse Time" vs "Execution Time" Means

This distinction matters for imports:

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

When Airflow parses a DAG file, it calls the `@dag`-decorated function (e.g. `dag = my_pipeline()`) to discover the task graph. This runs the function body at parse time. **But it does NOT run the code inside `@task` functions** — those are wrapped by the decorator and only execute later when Airflow actually runs the task. So a lazy import inside a `@task` body is safe at parse time.

This is why `snowflake_client.py` having top-level Snowflake imports is a risk: if it ever got imported at module level in a DAG file, any missing package would cause a parse failure. The fix is to keep those imports inside the function that actually needs them.

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

1. **Find the failing import** using the diagnostic commands above — look for `ModuleNotFoundError` or `ImportError` in the traceback
2. **Remove or lazy-ify the import**:
   - If unused: delete it
   - If optional (e.g. Snowflake packages not yet configured): move inside the function body
3. **Redeploy**: `./scripts/deploy.sh`
4. **Force reserialize** if the UI still shows the error after deploy:
   ```bash
   ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags reserialize"
   ```

### Prevention

- Never import gitignored files (`api_key.py`, `constants.py`, `db_config.py`) in library modules that other DAGs import — only import them inside the DAG file itself or lazily inside task bodies
- Any package that isn't guaranteed to be installed (Snowflake, Kafka, etc.) should be imported lazily inside the function that uses it, not at the top of the file

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

1. **Check your DAG file** (e.g., `dag_stocks.py`):
   ```bash
   tail -5 airflow/dags/dag_stocks.py
   ```
   Should show:
   ```python
   dag = stock_market_pipeline()  # ← Variable assignment
   ```

2. **If missing the assignment**, add it:
   ```python
   # Change from:
   stock_market_pipeline()

   # To:
   dag = stock_market_pipeline()
   ```

3. **Deploy the fix**:
   ```bash
   ./scripts/deploy.sh
   ```

4. **Force Airflow to reload DAGs** (re-scan /opt/airflow/dags/ and rebuild the DAG database):
   ```bash
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags reserialize
   ```
   **Why this step is needed:**
   - Airflow caches DAG metadata in its database (PostgreSQL)
   - When you deploy a new DAG file, the scheduler scans `/opt/airflow/dags/` periodically (default: every 30 seconds)
   - If the scheduler is slow to discover the new DAG, reserialize forces an immediate scan and database update
   - **Expected output**: `Setting next_dagrun for Stock_Market_Pipeline to...` (DAG is now registered)

5. **Verify DAG is discovered**:
   ```bash
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags list | grep "Stock_Market_Pipeline"
   ```
   Should return:
   ```
   Stock_Market_Pipeline | /opt/airflow/dags/dag_stocks.py | airflow | False | dags-folder | None
   ```

---

## Issue: All Pods `CreateContainerConfigError` After `helm upgrade` (Airflow Major Version)

### Symptoms
- Every pod in `airflow-my-namespace` is in `CreateContainerConfigError` or `Init:CrashLoopBackOff`
- `kubectl describe pod <any-pod>` shows: `Error: secret "airflow-webserver-secret-key" not found`
- No migration job is running or has recently run
- `helm upgrade` keeps timing out

### Why This Happens

This occurs when upgrading from Airflow 2.x to 3.x. The Airflow 2.x Helm chart created a secret named `airflow-webserver-secret-key`. The 3.x chart does NOT create it (replaced by `airflow-api-secret-key`). But the chart's default settings still have `enableBuiltInSecretEnvVars.AIRFLOW__WEBSERVER__SECRET_KEY: true`, which injects that env var — referencing the nonexistent secret — into every pod spec.

Because the migration job pod also has this issue, the migration job never starts. Without the migration, all other pods' init containers wait forever and crash. Everything is blocked by one missing chart default.

### Diagnosis

```bash
# Confirm the secret is missing
ssh ec2-stock "kubectl get secrets -n airflow-my-namespace | grep webserver"
# Should show nothing — airflow-webserver-secret-key doesn't exist in 3.x

# Confirm the pod error
ssh ec2-stock "kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace | grep -A2 'Error:'"
# Expect: Error: secret "airflow-webserver-secret-key" not found

# Check DB migration state (was the migration job ever able to run?)
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-postgresql-0 -- \
  env PGPASSWORD=postgres psql -U postgres -d postgres \
  -c 'SELECT version_num FROM alembic_version;'"
# If it returns 686269002441 (or another 2.x revision), migration hasn't run yet
```

### Fix

1. Add to `airflow/helm/values.yaml`:
   ```yaml
   enableBuiltInSecretEnvVars:
     AIRFLOW__WEBSERVER__SECRET_KEY: false
   ```

2. Sync and upgrade:
   ```bash
   scp airflow/helm/values.yaml ec2-stock:~/airflow/helm/values.yaml
   ssh ec2-stock "helm upgrade airflow apache-airflow/airflow \
     -n airflow-my-namespace \
     --version 1.20.0 \
     -f ~/airflow/helm/values.yaml \
     --atomic=false --timeout 2m"
   ```

3. If any StatefulSet pods (scheduler-0, triggerer-0) are still stuck with the old spec, force-recreate them:
   ```bash
   ssh ec2-stock "kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace"
   ssh ec2-stock "kubectl delete pod airflow-triggerer-0 -n airflow-my-namespace"
   ```

4. Confirm migration completed:
   ```bash
   ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-postgresql-0 -- \
     env PGPASSWORD=postgres psql -U postgres -d postgres \
     -c 'SELECT version_num FROM alembic_version;'"
   # Expect: 509b94a1042d (Airflow 3.1.8 head)
   ```

---

## Issue: Helm Upgrade Stuck — "another operation (install/upgrade/rollback) is in progress"

### Symptoms
- `helm upgrade` immediately fails with: `Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress`
- The cluster seems idle — no active deploy running

### Why This Happens

A previous `helm upgrade` process was killed (e.g., terminal closed, timeout) while Helm had already written a `pending-upgrade` status secret. Helm uses this to lock against concurrent upgrades. If the process was killed instead of completing, the lock is never released.

### Fix

```bash
# Find the stuck pending-upgrade release
ssh ec2-stock "kubectl get secret -n airflow-my-namespace \
  -l 'owner=helm,name=airflow' \
  -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.labels.status}{\"\\n\"}{end}'"
# Look for a line ending in 'pending-upgrade'

# Delete that specific secret to release the lock
ssh ec2-stock "kubectl delete secret sh.helm.release.v1.airflow.vN -n airflow-my-namespace"
# Replace vN with the actual revision number from the above output

# Also check for and kill any lingering helm process on EC2
ssh ec2-stock "ps aux | grep helm | grep -v grep"
ssh ec2-stock "kill <PID>"  # if a process is still running

# Now retry the upgrade
ssh ec2-stock "helm upgrade airflow apache-airflow/airflow ..."
```

---

## How Deploy.sh Validates DAG Files (Deployment Best Practices)

### Pre-flight Checks

When you run `./scripts/deploy.sh`, **before syncing to EC2**, it validates:

1. **Python syntax** — Catches typos, indentation errors, missing colons
   ```bash
   ✓ All DAG files have valid Python syntax
   ```

2. **Module imports** — Catches missing local modules (stock_client, file_logger, etc.)
   ```bash
   ✓ dag_stocks imports successfully
   ✓ dag_weather imports successfully
   ```

3. **Secret injection** — Each DAG validates that required Kubernetes secrets are available:
   ```python
   # In dag_stocks.py and dag_weather.py (added after imports):
   _required_secrets = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"]
   _missing_secrets = [k for k in _required_secrets if not os.getenv(k)]
   if _missing_secrets:
       raise RuntimeError(f"Missing Kubernetes secrets: {_missing_secrets}")
   ```

### Why This Matters

**Without validation:**
- Deploy file → pod starts but crashes → CrashLoopBackOff → read 200 lines of logs → find typo → fix locally → redeploy → repeat

**With validation:**
- Deploy file → validation fails locally → see 5-line error → fix → redeploy → success

This shifts debugging from "hours in logs" to "minutes locally".

### If Validation Fails

1. **Syntax error** — Check the Python file for typos, mismatched quotes, indentation
2. **Import error** — Verify the missing module exists in `airflow/dags/`
3. **Secret error** — Kubernetes secret not mounted; run in pod: `kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace` and check environment variables section

---

## Issue: DAG Files Not Visible in Airflow Pod

### Symptoms
- DAG files exist on EC2 but don't appear in the pod
- Airflow doesn't recognize new DAGs
- Scheduler logs show no errors, but DAGs don't appear in UI

### Diagnosis Steps

1. **Verify files exist on EC2**:
   ```bash
   ssh ec2-stock ls -la /home/ubuntu/airflow/dags/
   ```

2. **Check what's in the pod**:
   ```bash
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls -la /opt/airflow/dags/
   ```

3. **Compare the files** — Do they match?
   - If not, proceed to step 4
   - If yes, the issue is in Airflow's DAG parsing, not the volume mount

4. **Check PersistentVolume configuration**:
   ```bash
   ssh ec2-stock kubectl describe pv dag-pv
   ```
   Look for: `Source: HostPath: Path:`

5. **Compare paths**:
   - What does deploy.sh sync to? Check `scripts/deploy.sh` line 33:
     ```bash
     rsync -avz --progress airflow/dags/ "$EC2_HOST:$EC2_DAG_PATH/"
     # EC2_DAG_PATH is defined on line 9
     ```
   - What is the PV pointing to? From step 4 above
   - **Are they the same?** If not, this is your issue.

### Solution: Fix PersistentVolume Path

If PV is pointing to wrong path, delete and recreate it:

```bash
# 1. Delete the PVC (will cascade unbind from PV)
ssh ec2-stock kubectl delete pvc dag-pvc -n airflow-my-namespace

# 2. Remove finalizers from PV (makes it deletable)
ssh ec2-stock kubectl patch pvc dag-pvc -n airflow-my-namespace \
  --type merge -p '{"metadata":{"finalizers":null}}'

# 3. Force delete the PV
ssh ec2-stock kubectl delete pv dag-pv --grace-period=0 --force

# 4. Update the manifest with correct path
# Edit: airflow/manifests/pv-dags.yaml
# Change: hostPath.path to match deploy.sh sync destination

# 5. Recreate PV and PVC
ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/pv-dags.yaml
ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/pvc-dags.yaml

# 6. Restart scheduler pod
ssh ec2-stock kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace

# 7. Verify files appear
sleep 10
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  ls -la /opt/airflow/dags/
```

---

## Issue: DAG Appears After Deploy, Then Disappears ~90 Seconds Later (Processor Cache Stale)

### Symptoms
- DAG is visible in `airflow dags list` and Airflow UI immediately after deploying
- After ~90 seconds (exact timing varies), DAG disappears or marks `is_stale: True`
- Only affects newly deployed DAGs, not existing ones
- Weather/other DAGs in same folder work fine
- Scheduler logs show DAG is parsed successfully
- Running `airflow dags reserialize` brings it back temporarily, but it disappears again after 90s

### Root Cause: Kubernetes Filesystem Cache

When you deploy new DAG files to EC2 and K8s syncs them, **both Scheduler and Processor pods should see the same files**. However, on shared K8s volumes, the Processor pod may cache an old directory view:

```
Scheduler sees:   /opt/airflow/dags/dag_stocks.py    (inode 84268967, current)
Processor sees:   /opt/airflow/dags/ (inode 142630362, from June 2025, no dag_stocks.py)
```

When Airflow's sync cycle checks if the DAG file exists, it queries the Processor's stale view and can't find it → marks DAG stale.

### Diagnosis

1. **Verify Scheduler can see the file**:
   ```bash
   kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
     ls /opt/airflow/dags/dag_stocks.py
   ```
   Should show: `/opt/airflow/dags/dag_stocks.py` ✅

2. **Check if Processor sees the file**:
   ```bash
   # Get the processor pod name
   PROC_POD=$(kubectl get pod -l component=dag-processor -n airflow-my-namespace -o jsonpath='{.items[0].metadata.name}')

   # Try to list the file
   kubectl exec $PROC_POD -n airflow-my-namespace -- \
     ls /opt/airflow/dags/ | grep dag_stocks
   ```
   If nothing returns → processor has stale cache ❌

3. **Check DAG staleness status**:
   ```bash
   kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
     airflow dags details Stock_Market_Pipeline | grep is_stale
   ```

### Solution: Restart Processor Pod (Clear Cache)

```bash
# Delete all processor pods
kubectl delete pod -l component=dag-processor -n airflow-my-namespace

# K8s will automatically restart them with fresh filesystem view
# Wait 30-60 seconds for pod to restart
sleep 60

# Verify fix
PROC_POD=$(kubectl get pod -l component=dag-processor -n airflow-my-namespace -o jsonpath='{.items[0].metadata.name}')
kubectl exec $PROC_POD -n airflow-my-namespace -- \
  ls /opt/airflow/dags/dag_stocks.py
# Should now show the file ✅
```

### Prevention

**When deploying new DAG files**, restart both Scheduler and Processor pods to guarantee fresh filesystem views:

```bash
# Restart Scheduler
kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace

# Restart Processors
kubectl delete pod -l component=dag-processor -n airflow-my-namespace

# Wait for both to come back up
sleep 60
kubectl get pods -n airflow-my-namespace
```

Or, alternatively, deploy to a ConfigMap instead of a shared volume (more complex but avoids cache issues entirely).

---

## Issue: DAG Appears Briefly, Then Disappears from Airflow UI

### Symptoms
- DAG shows up in `airflow dags list` and Airflow UI after deploying or running `reserialize`
- After ~1 minute (next scheduler parse cycle), DAG vanishes from UI
- Status shows "Failed" when visible
- But tasks may have executed successfully (Flask dashboard or database shows data)

### Root Cause: Dynamic DAG Configuration

The most common cause is a **dynamic `start_date`** that changes on every Airflow parse cycle:

```python
# ✗ WRONG - start_date changes every parse cycle
start_date=pendulum.now("America/New_York").subtract(days=1)

# Why it breaks:
# - pendulum.now() evaluates at parse time (~5 second intervals)
# - Each evaluation produces a different timestamp
# - Airflow detects "configuration drift" and rejects DAG as invalid
# - DAG appears → parse again → config changed → reject → disappear
```

### Solution: Use Fixed Past Date

1. **Identify the problem**:
   ```bash
   # Check DAG's start_date in the pod:
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 << 'EOF'
   import sys
   sys.path.insert(0, '/opt/airflow/dags')
   from dag_stocks import dag
   print(f"start_date: {dag.start_date}")
   EOF

   # If the timestamp changes on each run, it's the dynamic start_date issue
   ```

2. **Replace dynamic date with fixed past date**:
   ```python
   # Change from:
   start_date=pendulum.now("America/New_York").subtract(days=1)

   # To:
   start_date=pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York")
   ```

3. **Deploy and rediscover**:
   ```bash
   # Deploy fix
   ./scripts/deploy.sh

   # Force scheduler to re-parse DAGs
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags reserialize
   ```

4. **Verify DAG is stable**:
   ```bash
   # Wait 35+ seconds (one parse cycle)
   sleep 35

   # Check if DAG is still visible
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags list | grep "Stock_Market_Pipeline"

   # Should show the DAG (doesn't disappear anymore)
   ```

### Why This Matters

Airflow's **immutability principle** requires that a DAG's configuration stay the same across parse cycles. Dynamic values like `pendulum.now()` violate this, causing the scheduler to:
1. Accept the DAG on first parse
2. Detect "configuration changed" on second parse
3. Reject it as invalid
4. Remove it from the UI

**Fixed past dates** satisfy the "must be in the past" requirement without changing on each parse.

### Examples of Correct start_dates

```python
# All of these are correct (immutable):
start_date=pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York")
start_date=datetime(2025, 3, 29, 0, 0, 0)
start_date=pendulum.parse("2025-03-29")

# All of these are WRONG (dynamic):
start_date=pendulum.now()                              # ✗
start_date=pendulum.now().subtract(days=1)            # ✗
start_date=datetime.now() - timedelta(days=1)         # ✗
```

---

## Issue: DAG Tasks Failing (Generic)

### Quick Diagnosis

1. **Check scheduler logs for errors**:
   ```bash
   ssh ec2-stock kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50 | grep -i error
   ```

2. **Check task logs in Airflow UI**:
   - Navigate to http://localhost:30080
   - Click the DAG name
   - Click the failed task
   - Read the "Logs" tab

3. **Check pod can reach external resources**:
   ```bash
   # Test database connection
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     bash -c 'python3 -c "import socket; socket.create_connection((\"<MARIADB_PRIVATE_IP>\", 3306), timeout=5); print(\"✓ DB reachable\")"'

   # Test API connectivity
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     bash -c 'curl -s https://api.example.com/ | head -c 100'
   ```

4. **Restart the pod to clear stale connections**:
   ```bash
   ssh ec2-stock kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace
   ```

---

## Issue: Task State Synchronization Error

### Symptoms

- Scheduler logs show error: "Executor reported that the task instance finished with state success, but the task instance's state attribute is running"
- Task may appear to complete successfully in Airflow UI despite the error message
- Error appears in scheduler logs but doesn't necessarily cause task failure
- Occurs intermittently, often under high parallelism or rapid task completion

### Example Error Message

```
[error] Executor LocalExecutor(parallelism=32) reported that the task instance
<TaskInstance: API_Weather-Pull_Data.extract scheduled__2026-03-31T02:18:51.659191+00:00 [running]>
finished with state success, but the task instance's state attribute is running.
Learn more: https://airflow.apache.org/docs/apache-airflow/stable/troubleshooting.html#task-state-changed-externally
[airflow.task] loc=taskinstance.py:1526
```

### Root Cause

This is a known Airflow issue related to task state synchronization. A race condition occurs between:
- The executor reporting task completion (success)
- The task instance state manager updating the task's state

Under high parallelism or when tasks complete very quickly, the state synchronization can lag, causing the executor and task instance to temporarily disagree on state.

### Current Status

**Non-critical**: Tasks usually complete successfully despite the error message. The error is a logging artifact rather than a functional failure.

### Diagnostic Steps

1. **Check scheduler logs for this specific error**:
   ```bash
   kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=100 | \
     grep "finished with state success.*is running"
   ```

2. **Verify the affected task actually completed**:
   ```bash
   # Check Airflow UI: Task should show success status
   # Or check task logs: Look for successful execution output
   ```

3. **Monitor if it recurs**:
   ```bash
   # Watch logs continuously
   kubectl logs -f airflow-scheduler-0 -n airflow-my-namespace | \
     grep "finished with state success.*is running"
   ```

### Mitigation Steps

If this error recurs frequently:

1. **Reduce LocalExecutor parallelism** (if applicable):
   - Edit `airflow/manifests/` configuration
   - Reduce `parallelism` from 32 to 16-24
   - Restart scheduler pod to apply

2. **Monitor task completion**:
   - Verify tasks are completing (not hanging)
   - Check Airflow UI task logs for actual errors
   - Use validation endpoint to verify data is being inserted

3. **Restart scheduler pod** (if tasks appear stuck):
   ```bash
   kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace
   ```

### References

- Airflow Documentation: https://airflow.apache.org/docs/apache-airflow/stable/troubleshooting.html#task-state-changed-externally

---

## Issue: SSH Post-Quantum Key Exchange Warning

### Solution

**Option 1: Upgrade OpenSSH on EC2** (recommended)
```bash
ssh ec2-stock
sudo yum update openssh-server openssh-clients -y
sudo systemctl restart sshd
```

**Option 2: Add SSH config workaround**
Edit `~/.ssh/config`:
```
Host ec2-stock
  HostKeyAlgorithms=ssh-ed25519,ecdsa-sha2-nistp256
  KexAlgorithms=curve25519-sha256,ecdh-sha2-nistp256
```

---

## Issue: All Static Assets Fail — "Network Connection Was Lost" (OOMKill)

**Symptoms:**
- Airflow UI loads but has no styling. Browser DevTools shows 10+ simultaneous "network connection was lost" errors for every CSS/JS file (`main.js`, `bootstrap.min.js`, `ab.css`, etc.) — all failing at once.
- **Or:** You navigate away from the UI, come back a few minutes later, and get "server unexpectedly dropped the connection" / SSH tunnel reports `channel N: open failed: connect failed: Connection refused`. The pod OOMKilled while you were away; K3S NodePort has no endpoint to route to.

Both are the same root cause — the api-server pod was OOMKilled. The difference is only in timing: static-asset errors mean you were watching mid-crash; connection-refused means you returned after it already crashed and restarted (or is restarting).

**Root cause:** The api-server pod exceeded its memory limit and was OOMKilled. Kubernetes force-kills the pod; all in-flight HTTP connections drop simultaneously, including the browser's CSS/JS requests. If you see a *single* API endpoint fail, suspect a DAG parse error (see [Fix DAG Parse Errors runbook](RUNBOOKS.md#16-fix-dag-parse-errors--err_network-on-grid-view)). If *all* static files fail at once, suspect a pod restart.

```bash
# Confirm OOMKill — look for "OOMKilled" in last state (Airflow 3.x: component=api-server, not webserver)
kubectl describe pod -l component=api-server -n airflow-my-namespace | grep -A5 "Last State:"

# Check live memory usage
kubectl top pod -n airflow-my-namespace

# Check restart count — a high count confirms repeated OOMKills
kubectl get pods -n airflow-my-namespace | grep api-server
```

**Fix applied (2026-04-06):** Increased `apiServer` memory limit from `1Gi` → `2Gi` and added `AIRFLOW__API_SERVER__WORKERS=2` in `values.yaml` — same fix used for `webserver` in Airflow 2.x (OOMKilled at 1Gi with 4 gunicorn workers × ~300MB provider load each). If you see this again, check that `values.yaml` wasn't reverted and that `apiServer.resources.limits.memory` is `2Gi`. See [Runbook #17](RUNBOOKS.md#17-fix-static-assets-failing-oomkill--network-connection-lost).

---

## Issue: Deploy.sh Changes Not Reflected in Cluster

### Possible Causes

1. **DAG files synced, but PV pointing to old location** → See "DAG Files Not Visible" above

2. **values.yaml changed but `helm upgrade` not run** → deploy.sh Step 2d handles this automatically. Syncing the file to EC2 does NOT apply the changes to the live cluster — only `helm upgrade` does:
   ```bash
   ./scripts/deploy.sh  # includes Step 2d: helm upgrade
   # Or run manually on EC2:
   ssh ec2-stock "helm upgrade airflow apache-airflow/airflow -n airflow-my-namespace --version 1.20.0 --atomic=false --timeout 2m -f ~/airflow/helm/values.yaml"
   ```

3. **Kubernetes manifests not applied** → Run:
   ```bash
   # From Mac:
   ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/

   # Or manually apply specific manifests:
   ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/pv-dags.yaml
   ```

3. **Scheduler pod needs restart** → Run:
   ```bash
   ssh ec2-stock kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace
   ```

4. **ECR credentials expired** (for Flask dashboard):
   ```bash
   # deploy.sh handles this automatically, but you can refresh manually:
   ssh ec2-stock "
   aws ecr get-login-password --region us-east-1 \
     | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com
   "
   ```

---

## Common Commands Reference

### Check Everything is Running

```bash
# Airflow pods
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# Scheduler pod logs
ssh ec2-stock kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50

# PersistentVolume status
ssh ec2-stock kubectl get pv,pvc -A | grep dag

# K3S cluster status
ssh ec2-stock kubectl cluster-info
ssh ec2-stock kubectl get nodes
```

### Manual DAG Trigger (if needed)

```bash
# Trigger specific DAG run from EC2
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger -e '2026-03-30' 'Stock_Market_Pipeline'"
```

### Check Database Tables

```bash
# From EC2 MariaDB
ssh ec2-stock "mariadb -u airflow_user -p'[PASSWORD]' -h <MARIADB_PRIVATE_IP> -e 'SHOW TABLES;'"

# From pod (if mariadb-client installed)
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  mariadb -u airflow_user -p'[PASSWORD]' -h <MARIADB_PRIVATE_IP> -e 'SHOW TABLES;'"
```

---

## Issue: Airflow UI (Port 30080) Drops Connection — Service Has No Endpoints

### Symptoms
- `http://localhost:30080` fails immediately: "server unexpectedly dropped the connection"
- SSH tunnel is open and working (dashboard on 32147 loads fine)
- All Airflow pods show `Running` with `0` restarts

### Root Cause: Service Selector Mismatch

The NodePort service routes traffic to pods by matching **labels**. If the selector doesn't match any pod's labels, the service has no endpoints and drops all connections.

Confirm this is the cause:
```bash
kubectl get endpoints -n airflow-my-namespace airflow-service-expose-ui-port
# If ENDPOINTS shows <none>, the selector matches nothing
```

Compare the service selector against the actual pod labels:
```bash
kubectl describe svc -n airflow-my-namespace airflow-service-expose-ui-port | grep Selector
kubectl get pods -n airflow-my-namespace --show-labels
```

**Known instance (2026-04-05):** After upgrading Airflow 2.x → 3.x, `service-airflow-ui.yaml` still had `component: webserver` (the 2.x label). In Airflow 3.x the UI/API pod is labeled `component: api-server` — no `webserver` pod exists. The selector matched zero pods → `<none>` endpoints → connection dropped.

### Solution

1. **Edit `airflow/manifests/service-airflow-ui.yaml`** — set selector to `component: api-server` (Airflow 3.x):
   ```yaml
   selector:
     component: api-server  # Airflow 3.x (was: webserver — 2.x only)
     release: airflow
   ```

2. **Re-apply the manifest on EC2:**
   ```bash
   rsync -avz airflow/manifests/service-airflow-ui.yaml ec2-stock:/home/ubuntu/airflow/manifests/
   ssh ec2-stock 'kubectl apply -f ~/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace'
   ```

3. **Verify endpoints populate:**
   ```bash
   ssh ec2-stock 'kubectl get endpoints -n airflow-my-namespace airflow-service-expose-ui-port'
   # Should show: 10.42.x.x:8080  (not <none>)
   ```

4. **Test the port:**
   ```bash
   ssh ec2-stock 'curl -s -o /dev/null -w "%{http_code}" http://localhost:30080/api/v2/monitor/health'
   # Should return: 200
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

---

## Issue: `TypeError: undefined is not an object (evaluating 'moment.tz')` in Browser Console

### Symptoms
- Browser console shows on the Airflow Home Page:
  ```
  [Error] TypeError: undefined is not an object (evaluating 'moment.tz')
      (anonymous function) (jquery-latest.js:...)
  ```
- Airflow UI still works correctly — this is cosmetic only

### Root Cause
A known Airflow 3.x bug in the legacy Flask-AppBuilder (FAB) components still embedded in some pages. The `AIRFLOW__WEBSERVER__DEFAULT_UI_TIMEZONE` env var (set in `values.yaml`) prevents `moment.tz.guess()` (auto-detect timezone), but FAB also calls `moment.tz(date, tz)` to format dates, which requires `moment-timezone.js` to be loaded synchronously. The script loading order in Airflow 3.x does not guarantee this. The fix would be a one-line change to a Jinja2 template inside Airflow's own source — moving the `<script src="moment-timezone.js">` tag to load before the FAB date-rendering script — but this lives in the Airflow project, not here.

### What Flask-AppBuilder is (and isn't)
FAB is a dependency **inside Airflow** — it's the old framework Airflow used to build its own web UI pages. It has nothing to do with our DAG code. Our DAGs use `from airflow.sdk import dag, task, XComArg`, which is the modern Airflow 3.x API. FAB is being phased out by the Airflow project itself: with each new Airflow release, more UI pages are rewritten in React, and as each page is converted the `moment.tz` error disappears from that page. This happens automatically on a version upgrade — no changes to our code are required.

### Options if you want to fix it
| Option | What it involves | Verdict |
|--------|-----------------|---------|
| **Wait for upstream fix** | Apache Airflow merges a template patch; we pick it up via a normal `helm upgrade` | Best option — zero effort, happens automatically |
| **Custom Docker image** | Build `FROM apache/airflow:3.1.8`, overwrite the offending FAB template file, push to ECR, point Helm at it | Fragile — breaks on every Airflow version bump; not worth it for a cosmetic error |
| **Do nothing** | Error only appears in the browser developer console (F12 → Console), never visible to users | Correct choice for this project |

### Status
Non-blocking. Only visible in browser developer console — not shown to users. Cannot be fixed via Helm config. Resolves automatically when Airflow upgrades the affected UI page from FAB to React.

---

## Issue: `404 Not Found` for Task Instance URL — "Mapped Task Instance ... was not found"

### Symptoms
Navigating to a bookmarked Airflow URL (saved before the 3.x upgrade) shows:
```
404 Not Found
The Mapped Task Instance with dag_id: `...`, run_id: `...`, task_id: `...`, and map_index: `-1` was not found
```

### Root Cause
Airflow 2.x API URLs represented **every** task instance — including non-mapped (regular) tasks — with `map_index: -1` as a sentinel value. Airflow 3.x changed the task instance API: non-mapped tasks no longer use `map_index` in the endpoint path, so any 2.x deep-link URL that includes `map_index=-1` returns 404 in 3.x.

This does **not** mean the task failed or that data is missing. It means the URL format is outdated.

### Fix
Discard the old bookmark. Navigate to the task instance via the Airflow 3.x UI:
1. Open the Airflow UI → click the DAG name
2. Click a run in the **Runs** grid
3. Click the task name in the task grid

### Notes
- Only affects saved/bookmarked URLs from before the upgrade; all new links generated by the 3.x UI are correct
- Confirmed non-issue: all DAG runs and task states remain accessible and correct via the new navigation path

---

## Prevention Checklist

When making infrastructure changes:

- [ ] Update `deploy.sh` paths
- [ ] Update K8s manifests to match
- [ ] Test `deploy.sh` with dry-run or test branch first
- [ ] Verify files on EC2 after deploy
- [ ] Verify files in pod after pod restart
- [ ] Check Airflow logs for DAG parsing errors
- [ ] Monitor first DAG run for execution errors

---

## Issue: `BuildKit is enabled but the buildx component is missing or broken`

### Symptoms
`deploy.sh` Step 4 fails during the Docker build on EC2 with:
```
ERROR: BuildKit is enabled but the buildx component is missing or broken.
       Install the buildx component to build images with BuildKit:
       https://docs.docker.com/go/buildx/
```

### What is BuildKit?
BuildKit is Docker's modern build engine, introduced as opt-in in Docker 18.09 and made
the **default in Docker 23+**. It replaces the legacy "classic" builder with a faster,
more parallel build graph, better layer caching, and new Dockerfile syntax features
(e.g. `--mount=type=cache` to cache pip/apt downloads between builds).

### What is `docker-buildx-plugin`?
`docker-buildx-plugin` is the apt package that installs the `buildx` binary — the CLI
frontend that BuildKit requires. When Docker runs a build with BuildKit enabled, it calls
`buildx` internally even if you use the classic `docker build` syntax. Without the
plugin, Docker has no way to invoke its own build engine and aborts with the above error.

### Root Cause
`deploy.sh` sets `DOCKER_BUILDKIT=1` explicitly, and Docker 23+ also enables BuildKit in
`daemon.json` by default. Either trigger requires `buildx`. On a fresh or recently
upgraded Ubuntu instance, `docker-buildx-plugin` is a separate apt package that isn't
always installed automatically alongside `docker.io` or `docker-ce`.

### Why not just remove `DOCKER_BUILDKIT=1`?
That would suppress our explicit opt-in, but if the Docker daemon has BuildKit on by
default (the case on Docker 23+), the build would still fail. Removing the env var is a
workaround that masks the real missing dependency rather than satisfying it.

### Why BuildKit matters for this project's future
As the pipeline grows to include Snowflake loaders, dbt runners, and Kafka consumers, each
will likely have its own container image. BuildKit features that become valuable then:
- **`--mount=type=cache`** — caches `pip install` and `apt-get` layers across rebuilds,
  cutting build times from ~2 min to ~10 s for unchanged dependencies
- **`--platform`** — builds multi-architecture images if you ever switch instance types
- **Parallel build graph** — independent `RUN` steps execute concurrently

### Fix
`docker-buildx-plugin` only exists in Docker's **official apt repo** (`download.docker.com`).
Ubuntu's default `docker.io` package (what this EC2 uses) does not include it, so
`apt-get install docker-buildx-plugin` fails with "Unable to locate package".

`deploy.sh` Step 4a instead downloads the buildx binary directly from GitHub releases —
the same source Docker's own install docs recommend when the plugin package isn't available:
```bash
if ! docker buildx version &>/dev/null; then
    BUILDX_VER=$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])")
    mkdir -p ~/.docker/cli-plugins
    curl -fsSL "https://github.com/docker/buildx/releases/download/${BUILDX_VER}/buildx-${BUILDX_VER}.linux-amd64" \
        -o ~/.docker/cli-plugins/docker-buildx
    chmod +x ~/.docker/cli-plugins/docker-buildx
fi
```
Docker discovers CLI plugins in `~/.docker/cli-plugins/` automatically — no apt or root
access needed after the download. The GitHub API call always fetches the latest stable
release so the script stays current without manual version bumps.

### Verification
Run `./scripts/deploy.sh`. Step 4 should complete with a successful push to ECR. On
subsequent deploys the `if` check short-circuits (buildx is already installed) so no
extra network traffic occurs.

## Issue: `kubectl` — `permission denied` reading `/etc/rancher/k3s/k3s.yaml`

### Symptoms
`deploy.sh` Step 2e (or any subsequent `kubectl` command) fails via SSH with:
```
error: error loading config file "/etc/rancher/k3s/k3s.yaml": open /etc/rancher/k3s/k3s.yaml: permission denied
```

### Root Cause
K3s writes its kubeconfig to `/etc/rancher/k3s/k3s.yaml` owned by `root` (mode 600). The
`ubuntu` SSH user has no read permission. Unlike standalone `kubectl`, the K3s kubectl binary
(symlinked to `k3s`) reads this path **directly** and ignores `~/.kube/config`, so copying the
file doesn't help — the permissions on the source file must be fixed.

### Fix
`deploy.sh` Step 1c runs on every deploy and makes the file world-readable:
```bash
sudo chmod 644 /etc/rancher/k3s/k3s.yaml
```
Runs on every deploy so permissions are restored even if K3s restarts and rewrites the file.

### Verification
Run `./scripts/deploy.sh` — Step 2e and all subsequent `kubectl` steps should succeed.

