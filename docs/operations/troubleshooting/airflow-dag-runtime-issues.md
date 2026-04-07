# Airflow DAG Issues — Runtime and Scheduling

Troubleshooting DAG disappearing after deploy, dynamic start_date problems, filesystem cache staleness, and task failures.

**See also:** [DAG Discovery Issues](airflow-dag-issues.md) | [Parent index](../TROUBLESHOOTING.md) | [DEBUGGING.md](../DEBUGGING.md)

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

2. **Check if Processor sees the file**:
   ```bash
   PROC_POD=$(kubectl get pod -l component=dag-processor -n airflow-my-namespace -o jsonpath='{.items[0].metadata.name}')
   kubectl exec $PROC_POD -n airflow-my-namespace -- \
     ls /opt/airflow/dags/ | grep dag_stocks
   ```
   If nothing returns → processor has stale cache

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
# Should now show the file
```

### Prevention

**When deploying new DAG files**, restart both Scheduler and Processor pods to guarantee fresh filesystem views:

```bash
kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
kubectl delete pod -l component=dag-processor -n airflow-my-namespace
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
   ./scripts/deploy.sh
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags reserialize
   ```

4. **Verify DAG is stable** (wait 35+ seconds for one parse cycle):
   ```bash
   sleep 35
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
     airflow dags list | grep "Stock_Market_Pipeline"
   ```

### Why This Matters

Airflow's **immutability principle** requires that a DAG's configuration stay the same across parse cycles. Dynamic values like `pendulum.now()` violate this, causing the scheduler to accept on first parse, then reject on second parse.

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

A known Airflow race condition between the executor reporting task completion and the task instance state manager updating state. Under high parallelism or when tasks complete very quickly, the state synchronization can lag.

### Current Status

**Non-critical**: Tasks usually complete successfully despite the error message. The error is a logging artifact rather than a functional failure.

### Diagnostic Steps

1. **Check scheduler logs for this specific error**:
   ```bash
   kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=100 | \
     grep "finished with state success.*is running"
   ```

2. **Verify the affected task actually completed** — check Airflow UI task status and logs

3. **Monitor if it recurs**:
   ```bash
   kubectl logs -f airflow-scheduler-0 -n airflow-my-namespace | \
     grep "finished with state success.*is running"
   ```

### Mitigation Steps

If this error recurs frequently:

1. **Reduce LocalExecutor parallelism** — reduce from 32 to 16-24, restart scheduler pod
2. **Monitor task completion** — verify tasks complete, check UI logs, use validation endpoint
3. **Restart scheduler pod** (if tasks appear stuck):
   ```bash
   kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace
   ```

### References

- Airflow Documentation: https://airflow.apache.org/docs/apache-airflow/stable/troubleshooting.html#task-state-changed-externally
