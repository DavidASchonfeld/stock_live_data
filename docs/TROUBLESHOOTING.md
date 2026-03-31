# Troubleshooting Guide

**Quick Navigation**
- Looking for general debugging approach? See [DEBUGGING.md](DEBUGGING.md)
- Need command explanations? See [COMMANDS.md](COMMANDS.md)
- Want to understand Airflow or ETL? See [ARCHITECTURE.md](ARCHITECTURE.md)
- Looking for term definitions? See [GLOSSARY.md](GLOSSARY.md)

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
     bash -c 'python3 -c "import socket; socket.create_connection((\"172.31.23.236\", 3306), timeout=5); print(\"✓ DB reachable\")"'

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
     | docker login --username AWS --password-stdin REDACTED_AWS_ACCOUNT_ID.dkr.ecr.us-west-2.amazonaws.com
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
ssh ec2-stock "mariadb -u airflow_user -p'[PASSWORD]' -h 172.31.23.236 -e 'SHOW TABLES;'"

# From pod (if mariadb-client installed)
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  mariadb -u airflow_user -p'[PASSWORD]' -h 172.31.23.236 -e 'SHOW TABLES;'"
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

