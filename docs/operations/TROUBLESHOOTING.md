# Troubleshooting Guide

**Quick Navigation**
- Looking for general debugging approach? See [DEBUGGING.md](DEBUGGING.md)
- Need command explanations? See [../reference/COMMANDS.md](../reference/COMMANDS.md)
- Want to understand Airflow or ETL? See [../architecture/SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md)
- Looking for term definitions? See [../reference/GLOSSARY.md](../reference/GLOSSARY.md)
- Failure mode catalog? See [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md)
- Prevention checklists? See [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md)

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
   ssh ec2-stock ls -la /home/ec2-user/airflow/dags/
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
ssh ec2-stock kubectl apply -f /home/ec2-user/airflow/manifests/pv-dags.yaml
ssh ec2-stock kubectl apply -f /home/ec2-user/airflow/manifests/pvc-dags.yaml

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

## Issue: Deploy.sh Changes Not Reflected in Cluster

### Possible Causes

1. **DAG files synced, but PV pointing to old location** → See "DAG Files Not Visible" above

2. **Kubernetes manifests not applied** → Run:
   ```bash
   # From Mac:
   ssh ec2-stock kubectl apply -f /home/ec2-user/airflow/manifests/

   # Or manually apply specific manifests:
   ssh ec2-stock kubectl apply -f /home/ec2-user/airflow/manifests/pv-dags.yaml
   ```

3. **Scheduler pod needs restart** → Run:
   ```bash
   ssh ec2-stock kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace
   ```

4. **ECR credentials expired** (for Flask dashboard):
   ```bash
   # deploy.sh handles this automatically, but you can refresh manually:
   ssh ec2-stock "
   aws ecr get-login-password --region us-west-2 \
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

## Prevention Checklist

When making infrastructure changes:

- [ ] Update `deploy.sh` paths
- [ ] Update K8s manifests to match
- [ ] Test `deploy.sh` with dry-run or test branch first
- [ ] Verify files on EC2 after deploy
- [ ] Verify files in pod after pod restart
- [ ] Check Airflow logs for DAG parsing errors
- [ ] Monitor first DAG run for execution errors

