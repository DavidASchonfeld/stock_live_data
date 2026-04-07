# Runbooks 1–2: Deploy Code Changes + Add a New DAG

> Part of the [Runbooks Index](../RUNBOOKS.md). For pre-flight checklists, see [PREVENTION_CHECKLIST.md](../PREVENTION_CHECKLIST.md).

---

## 1. Deploy Code Changes

**When:** You've modified DAG files, scripts, or manifests locally and want to push to EC2/K3s.

**Prerequisites:**
- SSH access to EC2 (`ssh ec2-stock` works)
- Changes committed to Git (source of truth)

**Steps:**

```bash
# 1. Validate locally
python -c "import sys; sys.path.insert(0,'airflow/dags'); import dag_stocks; import dag_weather"

# 2. Check for forbidden patterns
grep -n "pendulum.now\|datetime.now" airflow/dags/dag_*.py
# Must return zero matches

# 3. Verify PV path consistency
grep "EC2_DAG_PATH" scripts/deploy.sh
grep "path:" airflow/manifests/pv-dags.yaml
# Both paths must match

# 4. Run deploy
./scripts/deploy.sh

# 5. Restart pods to prevent filesystem cache staleness
ssh ec2-stock kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
ssh ec2-stock kubectl delete pod -l component=dag-processor -n airflow-my-namespace

# 6. Wait for pods to come back
sleep 60
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# 7. Verify DAGs visible
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list

# 8. Monitor first DAG run in the Airflow UI
```

**Success criteria:** All DAGs visible in `airflow dags list`, first post-deploy run completes successfully.

---

## 2. Add a New DAG

**When:** You want to add a new data pipeline (e.g., a new API source).

**Steps:**

```bash
# 1. Create the DAG file locally — use dag_stocks.py or dag_weather.py as a template

# 2. Mandatory configuration checklist:
#    - start_date: fixed past date (e.g., pendulum.datetime(2025, 3, 29, tz="America/New_York"))
#    - catchup=False
#    - Module-level variable: dag = my_new_pipeline()
#    - _required_secrets list for env var validation (inside @task, not module-level)

# 3. Create any new client scripts in airflow/dags/ — must be importable from the DAG

# 4. Test imports locally
python -c "import sys; sys.path.insert(0,'airflow/dags'); import dag_new_source"

# 5. If new DB table needed, add CREATE TABLE IF NOT EXISTS in the load task

# 6. Deploy using Runbook #1 above

# 7. Unpause the new DAG (Airflow 3.x pauses new DAGs by default)
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags unpause New_DAG_ID

# 8. Trigger a manual test run
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger New_DAG_ID

# 9. Watch the run complete
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list-runs New_DAG_ID

# 10. Verify data in MariaDB
```

**Success criteria:** DAG visible, unpaused, first run completes, data in MariaDB.
