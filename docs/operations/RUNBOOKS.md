# Operational Runbooks

Step-by-step playbooks for common operations. Each runbook is a complete procedure — follow it start to finish.

**Navigation:**
- Pre-flight checklists for each operation? → [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md)
- Something went wrong? → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Understanding why these steps matter? → [../architecture/COMPONENT_INTERACTIONS.md](../architecture/COMPONENT_INTERACTIONS.md)

---

## Table of Contents

1. [Deploy Code Changes](#1-deploy-code-changes)
2. [Add a New DAG](#2-add-a-new-dag)
3. [Rotate Database Credentials](#3-rotate-database-credentials)
4. [Rollback a Bad Helm Upgrade](#4-rollback-a-bad-helm-upgrade)
5. [Recover from Total Cluster Outage](#5-recover-from-total-cluster-outage)
6. [Backfill Missing Data](#6-backfill-missing-data)
7. [Update Flask Dashboard Image](#7-update-flask-dashboard-image)
8. [Change Working Location (IP Update)](#8-change-working-location-ip-update)
9. [Investigate Stale Data](#9-investigate-stale-data)
10. [Add a New API Data Source](#10-add-a-new-api-data-source)

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
# Must exit cleanly with no errors

# 2. Check for forbidden patterns
grep -n "pendulum.now\|datetime.now" airflow/dags/dag_*.py
# Must return zero matches

# 3. Verify PV path consistency
grep "EC2_DAG_PATH" scripts/deploy.sh
grep "path:" airflow/manifests/pv-dags.yaml
# Both paths must match

# 4. Run deploy
./scripts/deploy.sh
# Watch for errors at each step. Don't ignore warnings.

# 5. Restart pods to prevent filesystem cache staleness
ssh ec2-stock kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
ssh ec2-stock kubectl delete pod -l component=dag-processor -n airflow-my-namespace

# 6. Wait for pods to come back (60 seconds)
sleep 60
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# 7. Verify DAGs visible
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list

# 8. Monitor first DAG run
# Open Airflow UI at http://localhost:30080 (via SSH tunnel)
# Watch the DAG run complete successfully
```

**Success criteria:** All DAGs visible in `airflow dags list`, first post-deploy run completes successfully.

---

## 2. Add a New DAG

**When:** You want to add a new data pipeline (e.g., a new API source).

**Steps:**

```bash
# 1. Create the DAG file locally
# Use existing dag_stocks.py or dag_weather.py as a template

# 2. Mandatory configuration checklist:
#    - start_date: fixed past date (e.g., pendulum.datetime(2025, 3, 29, tz="America/New_York"))
#    - catchup=False
#    - Module-level variable: dag = my_new_pipeline()
#    - _required_secrets list for env var validation

# 3. Create any new client scripts in airflow/dags/ (or scripts/)
# They must be importable from the DAG file's location

# 4. Test imports locally
python -c "import sys; sys.path.insert(0,'airflow/dags'); import dag_new_source"

# 5. If new DB table needed, add CREATE TABLE IF NOT EXISTS
# in the load task, or pre-create via runbook

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
# Query the new table to confirm data was inserted correctly
```

**Success criteria:** DAG visible, unpaused, first run completes, data in MariaDB.

---

## 3. Rotate Database Credentials

**When:** Changing MariaDB password for security or after a suspected compromise.

**Steps:**

```bash
# 1. Update password in MariaDB (on EC2)
ssh ec2-stock
sudo mysql -u root
# In MariaDB shell:
ALTER USER 'airflow_user'@'10.42.%' IDENTIFIED BY 'NEW_PASSWORD_HERE';
ALTER USER 'airflow_user'@'<MARIADB_PRIVATE_IP>' IDENTIFIED BY 'NEW_PASSWORD_HERE';
FLUSH PRIVILEGES;
EXIT;

# 2. Update K8s Secret in airflow namespace
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=NEW_PASSWORD_HERE \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=ALPHA_VANTAGE_KEY=YOUR_API_KEY \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Update K8s Secret in default namespace (for Flask)
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=NEW_PASSWORD_HERE \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=ALPHA_VANTAGE_KEY=YOUR_API_KEY \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Restart ALL pods (secrets don't hot-reload)
kubectl rollout restart statefulset airflow-scheduler -n airflow-my-namespace
kubectl rollout restart deployment airflow-api-server -n airflow-my-namespace
kubectl rollout restart statefulset airflow-triggerer -n airflow-my-namespace
kubectl delete pod my-kuber-pod-flask -n default

# 5. Wait for pods to restart
sleep 60
kubectl get pods --all-namespaces

# 6. Verify credentials work
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- env | grep DB_PASSWORD
# Should show new password

# 7. Test end-to-end
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
# Watch run succeed — confirms DB access works

# 8. Update local reference
exit  # back to Mac
# Update infra_local.md with new password (gitignored)
```

**Success criteria:** All pods running with new credentials, DAG run completes successfully, dashboard loads.

---

## 4. Rollback a Bad Helm Upgrade

**When:** A `helm upgrade` broke something and you need to revert.

**Steps:**

```bash
# 1. Check Helm release history
ssh ec2-stock helm history airflow -n airflow-my-namespace
# Note the REVISION number of the last working version

# 2. Rollback to previous revision
ssh ec2-stock helm rollback airflow <PREVIOUS_REVISION> -n airflow-my-namespace
# Example: helm rollback airflow 3 -n airflow-my-namespace

# 3. Force-delete any pods stuck in CrashLoopBackOff
ssh ec2-stock kubectl delete pod airflow-scheduler-0 airflow-triggerer-0 -n airflow-my-namespace
# StatefulSets auto-recreate with rolled-back config

# 4. Wait for pods to stabilize
sleep 60
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# 5. Verify Airflow UI is accessible
# http://localhost:30080 via SSH tunnel

# 6. Verify DAGs are visible and running
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list

# 7. Check endpoints
ssh ec2-stock kubectl get endpoints -n airflow-my-namespace
# Must show IPs, not <none>
```

**Success criteria:** All pods Running, Airflow UI accessible, DAGs visible.

---

## 5. Recover from Total Cluster Outage

**When:** EC2 instance was stopped/restarted, or K3s crashed and nothing is working.

**Steps:**

```bash
# 1. Verify EC2 instance is running
# AWS Console → EC2 → Instances → check state
# If stopped: Start it. Note: public IP may change.

# 2. SSH into EC2
ssh ec2-stock
# If timeout: IP changed. Update security group (Runbook #8) and ~/.ssh/config.

# 3. Check K3s status
sudo systemctl status k3s
# If not active: sudo systemctl restart k3s
# Wait 30 seconds for K3s to initialize

# 4. Check all pods
kubectl get pods --all-namespaces
# Expect: most pods in Running or starting up
# PostgreSQL should come up first, then Airflow pods

# 5. If pods stuck in ImagePullBackOff (ECR token expired)
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com
# Then recreate the ECR credential secret
# (deploy.sh step 5 handles this — or run manually)

# 6. If pods stuck in Init:0/1 (PostgreSQL not ready)
kubectl get pods -n airflow-my-namespace | grep postgresql
# Wait for postgresql pod to reach Running
# All other pods will auto-unblock

# 7. Verify services have endpoints
kubectl get endpoints -A
# No <none> entries

# 8. Check PVs are Bound
kubectl get pv,pvc -A
# All should show Bound status

# 9. Verify data integrity
# Query latest rows in MariaDB to confirm data survived
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(f'mysql+pymysql://{os.environ[\"DB_USER\"]}:{os.environ[\"DB_PASSWORD\"]}@{os.environ[\"DB_HOST\"]}/{os.environ[\"DB_NAME\"]}')
with engine.connect() as c:
    for t in ['stock_daily_prices','weather_hourly']:
        r = c.execute(text(f'SELECT COUNT(*) FROM {t}')).scalar()
        print(f'{t}: {r} rows')
"

# 10. Trigger DAG runs to fill any gaps
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger API_Weather-Pull_Data

# 11. Re-establish SSH tunnel (from Mac)
exit
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

**Success criteria:** All pods Running, data intact, DAG runs complete, dashboard accessible.

---

## 6. Backfill Missing Data

**When:** DAG runs were missed (outage, paused DAG, failed runs) and you need to fill gaps.

**Steps:**

```bash
# 1. Identify the gap
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(f'mysql+pymysql://{os.environ[\"DB_USER\"]}:{os.environ[\"DB_PASSWORD\"]}@{os.environ[\"DB_HOST\"]}/{os.environ[\"DB_NAME\"]}')
with engine.connect() as c:
    r = c.execute(text('SELECT MAX(date) FROM stock_daily_prices')).scalar()
    print(f'Latest stock data: {r}')
    r = c.execute(text('SELECT MAX(time) FROM weather_hourly')).scalar()
    print(f'Latest weather data: {r}')
"

# 2a. For Stock data — trigger with specific execution date
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger -e '2026-03-30' Stock_Market_Pipeline
# Note: Alpha Vantage returns historical data, so one trigger
# fetches recent daily data regardless of execution date.
# Rate limit: 25 calls/day — plan backfills carefully.

# 2b. For Weather data — trigger
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger API_Weather-Pull_Data
# Open-Meteo returns forecast/recent data per call.

# 3. Monitor runs
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list-runs Stock_Market_Pipeline
# Wait for state: success

# 4. Verify data filled
# Re-run the query from step 1 to confirm latest dates advanced
```

**Success criteria:** Latest data timestamps match expected dates.

---

## 7. Update Flask Dashboard Image

**When:** You've changed Flask/Dash code and need to deploy a new image.

**Steps:**

```bash
# 1. Build new Docker image locally
cd dashboard
docker build -t stock-dashboard:latest .

# 2. Tag for ECR
docker tag stock-dashboard:latest \
  <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/stock-dashboard:latest

# 3. Push to ECR
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com
docker push <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/stock-dashboard:latest

# 4. Refresh ECR credentials on EC2
ssh ec2-stock "
aws ecr get-login-password --region us-west-2 | \
  docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com
"
# Then update the ecr-credentials K8s secret (deploy.sh step 5 does this)

# 5. Restart Flask pod to pull new image
ssh ec2-stock kubectl delete pod my-kuber-pod-flask -n default

# 6. Wait and verify
sleep 30
ssh ec2-stock kubectl get pods -n default
# Should show Running, not ImagePullBackOff

# 7. Test dashboard
# http://localhost:32147/dashboard/ via SSH tunnel
```

**Or** just run `./scripts/deploy.sh` which handles steps 1-6 automatically.

**Success criteria:** Flask pod Running with new image, dashboard shows expected changes.

---

## 8. Change Working Location (IP Update)

**When:** You're working from a new network (different public IP) and can't SSH into EC2.

**Steps:**

```bash
# 1. Find your current public IP
curl ifconfig.me

# 2. Go to AWS Console
# EC2 → Security Groups → find your EC2 instance's security group
# Edit inbound rules → find SSH rule (port 22)
# Change source IP to your current IP /32

# 3. Test SSH
ssh ec2-stock

# 4. Re-establish SSH tunnel
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock

# 5. Update local reference
# Edit infra_local.md with the new IP (gitignored)
```

**Success criteria:** SSH connects, Airflow UI and dashboard accessible via tunnel.

---

## 9. Investigate Stale Data

**When:** Dashboard is showing old data and you need to find out why.

**Steps:**

```bash
# 1. Check how stale the data is
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 -c "
from sqlalchemy import create_engine, text
import os
engine = create_engine(f'mysql+pymysql://{os.environ[\"DB_USER\"]}:{os.environ[\"DB_PASSWORD\"]}@{os.environ[\"DB_HOST\"]}/{os.environ[\"DB_NAME\"]}')
with engine.connect() as c:
    for t in ['stock_daily_prices','weather_hourly']:
        r = c.execute(text(f'SELECT MAX(imported_at) FROM {t}')).scalar()
        print(f'{t}: last import = {r}')
"

# 2. Check if DAGs are paused
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list
# Look for paused=True

# 3. Check recent DAG runs
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list-runs Stock_Market_Pipeline
# Look for: failed runs, no recent runs, or no runs at all

# 4. If runs are failing, check task logs
# Airflow UI → Click DAG → Click failed task → Logs tab
# Or from CLI:
ssh ec2-stock kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=100

# 5. Common causes:
#    - DAG paused → unpause it (Runbook note: Airflow 3.x pauses new DAGs)
#    - API rate limited → wait or check key
#    - DB credentials expired → Runbook #3
#    - Scheduler pod not running → check pod status

# 6. Once fixed, backfill if needed (Runbook #6)
```

---

## 10. Add a New API Data Source

**When:** You want to add a new external API (e.g., crypto prices, news sentiment, economic indicators).

**Design steps before writing code:**

1. **API Research**
   - What's the rate limit? (determines scheduling frequency)
   - What authentication is needed? (API key → add to K8s Secret)
   - What's the response format? (JSON structure for `json_normalize`)
   - What's the data freshness? (real-time, daily, hourly)

2. **Database Design**
   - What columns do you need? (define table schema)
   - What's the primary key / unique constraint? (prevent duplicates)
   - How does this relate to existing tables? (join keys)

3. **DAG Design**
   - Schedule interval (match API data freshness)
   - Retry policy (how many retries, what backoff)
   - Dependencies (does this DAG depend on other DAGs?)

4. **Implementation** (follow [Add a New DAG](#2-add-a-new-dag) runbook)
   - Create client script (API wrapper)
   - Create DAG file (extract → transform → load)
   - Add validation at each gate (see [DATA_FLOW.md](../architecture/DATA_FLOW.md))
   - Test locally, deploy, verify

5. **Dashboard Integration**
   - Add Flask endpoint to query new table
   - Add Dash visualization
   - Update Flask image (Runbook #7)

---

**Last updated:** 2026-03-31
