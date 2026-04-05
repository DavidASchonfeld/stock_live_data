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
11. [Enable / Disable Vacation Mode](#11-enable--disable-vacation-mode)
12. [Configure Slack Alerting](#12-configure-slack-alerting)

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
# Note: EDGAR_CONTACT_EMAIL is also stored here (kept out of git)
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=NEW_PASSWORD_HERE \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Update K8s Secret in default namespace (for Flask)
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=NEW_PASSWORD_HERE \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
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
aws ecr get-login-password --region us-east-1 | \
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
    for t in ['company_financials','weather_hourly']:
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
    r = c.execute(text('SELECT MAX(filed_date) FROM company_financials')).scalar()
    print(f'Latest financials data: {r}')
    r = c.execute(text('SELECT MAX(time) FROM weather_hourly')).scalar()
    print(f'Latest weather data: {r}')
"

# 2a. For Stock data — trigger with specific execution date
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger -e '2026-03-30' Stock_Market_Pipeline
# Note: SEC EDGAR returns ALL historical financial data in one call,
# so one trigger fetches everything. No daily rate limit concern
# (RateLimiter handles per-second throttling automatically).

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
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com
docker push <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com/stock-dashboard:latest

# 4. Refresh ECR credentials on EC2
ssh ec2-stock "
aws ecr get-login-password --region us-east-1 | \
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
    r = c.execute(text('SELECT MAX(filed_date) FROM company_financials')).scalar()
    print(f'company_financials: latest filed = {r}')
    r = c.execute(text('SELECT MAX(imported_at) FROM weather_hourly')).scalar()
    print(f'weather_hourly: latest import = {r}')
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

---

## 11. Enable / Disable Vacation Mode

**When:** You're leaving and want to stop all DAGs from calling external APIs, or you're back and want to resume normal operation.

### Two-layer protection

| Layer | Mechanism | Where to set | Survives DB wipe? |
|-------|-----------|--------------|-------------------|
| Primary | Airflow native **pause** | Airflow UI toggle | No |
| Guard | `VACATION_MODE` **Airflow Variable** | Admin → Variables | Yes (in code) |

Always enable **both** for maximum safety.

### Enable vacation mode (before leaving)

```bash
# Step 1 — Set the Airflow Variable (no SSH needed; use the UI)
# Airflow UI → Admin → Variables → "+" button
# Key: VACATION_MODE
# Value: true

# Step 2 — Pause both DAGs in the Airflow UI
# Airflow UI → DAGs list → click the toggle left of each DAG name
# Stock_Market_Pipeline   → paused (grayed out)
# API_Weather-Pull_Data   → paused (grayed out)

# Step 3 — Verify (optional, via SSH tunnel)
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list
# Both DAGs should show paused=True

# Step 4 — Verify the Variable is set
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow variables get VACATION_MODE
# Should print: true
```

**What happens when enabled:** Any scheduled run that starts will reach `extract()`, call `check_vacation_mode()`, and raise `AirflowSkipException`. The task (and all downstream tasks) are marked **Skipped** — not Failed. No API calls are made. No DB writes happen.

### Test that vacation mode is working

After enabling, verify the skip cascade fires correctly before you leave:

```bash
# Trigger a manual run on any DAG
# Airflow UI → Stock_Market_Pipeline → Trigger DAG ▶
# (or via CLI)
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
```

**Expected result in the Airflow UI task grid:**
- `extract` → **Skipped** (pink badge)
- `transform` → **Skipped** (pink badge)
- `load` → **Skipped** (pink badge)
- Overall run status → **Success** (green — skipped runs still count as success)

If any task shows **Failed** instead of **Skipped**, vacation mode is not working — check that the Variable value is exactly `true` (lowercase, no spaces).

---

### Audit past runs — confirm vacation mode fired

Every DAG run now records the VACATION_MODE value in the `extract` task log. To verify whether a specific run was skipped:

1. **Airflow UI → DAGs → `Stock_Market_Pipeline` → click a past run**
2. Click the `extract` task box → **"Log"**
3. Search the log for `VACATION_MODE =`
   - `VACATION_MODE = true` → vacation mode was active; tasks were skipped
   - `VACATION_MODE = false` → pipeline ran normally; API calls were made

You can also check all runs at once from the CLI:
```bash
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  grep -r "VACATION_MODE =" /opt/airflow/logs/dag_id=Stock_Market_Pipeline/
```

---

### Disable vacation mode (when you return)

```bash
# Step 1 — Update the Airflow Variable
# Airflow UI → Admin → Variables → click VACATION_MODE → change value to false
# (or delete the variable entirely — missing variable defaults to "false")

# Step 2 — Unpause both DAGs
# Airflow UI → DAGs list → click the toggle to unpause each DAG

# Step 3 — Trigger a manual run to confirm everything is working
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger API_Weather-Pull_Data
```

**Success criteria:** Both manual runs complete with `state: success`, new rows appear in `company_financials` and `weather_hourly`.

---

## 12. Configure Slack Alerting

> **Current status (as of 2026-03-31):** A Slack webhook URL has been generated and the alerting infrastructure is fully built, but it has **not been connected to a Slack account or workspace**. The system is currently running in **log-only mode** — no Slack notifications are actively being received. Follow this runbook when you're ready to activate live notifications.

**When:** Setting up Slack notifications for DAG failures, retries, and data staleness.

**Prerequisites:**
- A Slack workspace you control
- Permission to create Slack apps / incoming webhooks

### Create Slack Webhook

1. Go to https://api.slack.com/apps → **Create New App** → **From scratch**
2. Name it (e.g., "Stock Pipeline Alerts"), select your workspace
3. **Incoming Webhooks** → toggle **On** → **Add New Webhook to Workspace**
4. Choose the channel for alerts → **Allow**
5. Copy the webhook URL (looks like `https://hooks.slack.com/services/T.../B.../xxx`)

### Configure Locally (development)

```bash
# Add to your .env file at the repo root
echo 'SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx' >> .env
```

Without `SLACK_WEBHOOK_URL`, alerting runs in **log-only mode** — alerts are printed to stdout and PVC log files but not sent to Slack.

### Configure in Kubernetes (production)

```bash
# Update the db-credentials secret to include the webhook URL
# (same secret that stores DB_USER, DB_PASSWORD, etc.)
ssh ec2-stock
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=<DB_PASSWORD> \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --from-literal=SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart Airflow pods to pick up the new secret value
kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
kubectl delete pod -l component=dag-processor -n airflow-my-namespace

# Wait for pods to restart
sleep 60
kubectl get pods -n airflow-my-namespace
```

### Test the Alert

```bash
# Trigger a manual DAG run that will fail (e.g., temporarily break DB credentials)
# Or just check Slack channel after a natural failure/retry occurs

# To test log-only mode, leave SLACK_WEBHOOK_URL empty and check PVC logs:
ssh ec2-stock cat /home/ec2-user/airflow/dag-mylogs/*.txt | grep "ALERT"
```

### Adjust Staleness Thresholds

Set these environment variables (in `.env` locally, or in the K8s secret for production):

| Variable | Default | Meaning |
|----------|---------|---------|
| `STALENESS_THRESHOLD_HOURS_STOCKS` | 168 (7 days) | Alert if `company_financials` has no data newer than this |
| `STALENESS_THRESHOLD_HOURS_WEATHER` | 2 | Alert if `weather_hourly` has no data newer than this |

### Adjust Alert Cooldown

| Variable | Default | Meaning |
|----------|---------|---------|
| `ALERT_COOLDOWN_MINUTES` | 60 | Minimum minutes between repeated alerts for the same DAG+task or stale table |

With DAGs running every 5 minutes, the default 60-minute cooldown means at most 1 alert per hour per failure, instead of 12+.

**Alert state is stored as Airflow Variables** with keys prefixed `alert_last_sent:` — visible and editable under Admin → Variables.

- `alert_last_sent:<dag_id>:<task_id>` — task failure/retry cooldown
- `alert_last_sent:staleness:company_financials` — staleness cooldown
- `alert_last_sent:staleness:weather_hourly` — staleness cooldown

**To immediately re-arm alerts** (e.g., after investigating an issue and wanting the next failure to notify you again): delete the relevant Variable in Admin → Variables.

> Note: "Alert suppressed" ≠ "Alert broken" — if you expected a Slack message and didn't get one, check Admin → Variables for a recent `alert_last_sent:*` timestamp before assuming the webhook is down.

**Recovery notifications:** When a failing task succeeds again, a single `:green_circle: Task Recovered` message is sent automatically and the cooldown state is cleared.

### Vacation Mode Behavior

- **Failure/retry alerts always fire** — if a DAG fails during vacation instead of cleanly skipping, that means vacation mode is broken
- **Staleness alerts are silenced** during vacation mode — stale data is expected when pipelines are paused

**Success criteria:** Slack message appears in your channel when a DAG task fails or retries, or when data exceeds the staleness threshold.

---

---

## 13. Migrate EC2 to a New Region

**When:** Moving the EC2 instance to a different AWS region (e.g., us-west-2 → us-east-1).

**Prerequisites:**
- AWS Console access
- SSH key `.pem` file available locally
- No active DAG runs in progress

### Phase A — Pre-migration (local Mac)

```bash
# 1. Extract public key from .pem (needed to import into new region)
ssh-keygen -y -f /Users/David/Documents/Programming/Python/Data-Pipeline-2026/kafkaProjectKeyPair_4-29-2025.pem
# Save the output line (starts with ssh-rsa)

# 2. Document current security group inbound rules in AWS Console:
#    EC2 (us-west-2) → Security Groups → Inbound rules → write them all down
```

### Phase B — AWS Console (us-west-2)

1. **Create AMI:** EC2 → Instances → select instance → Actions → Image and templates → Create image
   - Name: `data-pipeline-migration-YYYYMMDD`
   - "No reboot": leave **unchecked** (ensures filesystem consistency)
   - **"Delete on termination" (in the storage section): leave checked (default)**
     > There are 3 separate things: (1) the **EC2 instance** — the virtual computer; (2) the **EBS volume** — its virtual hard drive; (3) the **AMI** — a backup photo of the hard drive stored separately in S3. "Delete on termination" only controls whether the hard drive is automatically thrown away when the instance is permanently shut down (terminated). Checked = yes, auto-delete the hard drive on termination (no orphaned volumes, no surprise charges). The AMI is completely separate and is never affected — it persists until you manually delete it yourself.
   - Wait for status "available" (5–20 min)
2. **Copy AMI to target region:** AMIs → select AMI → Actions → Copy AMI → Destination: `us-east-1`
   - Wait for "available" in us-east-1 (15–45 min)

> The AMI carries K3S etcd (all K8s Secrets intact), MariaDB data dir, Docker images, and `/home/ec2-user/`.

### Phase C — AWS Console (us-east-1)

3. **Import key pair:** EC2 → Key Pairs → Import key pair → paste the public key from Phase A
4. **Create security group:** EC2 → Security Groups → Create → add identical inbound rules from Phase A
5. **Create ECR repo:** ECR → Create repository → Name: `my-flask-app` (Private)
   - New registry URI: `683010036255.dkr.ecr.us-east-1.amazonaws.com`
6. **Launch instance:** AMIs → select copied AMI → Launch instance from AMI
   - Instance type: `t3.large` (2 vCPU, 8 GB — downsized from t3.xlarge; safe to test since this is a fresh launch)
   - Key pair, security group: from steps 3–4 above
   - **IAM role: must be manually re-attached** — AMIs copy the disk but NOT the IAM role assignment. Without it, the instance has no credentials to talk to ECR, and `./scripts/deploy.sh` will fail at Step 4 with "Unable to locate credentials". Set it here at launch time: under **Advanced details → IAM instance profile**, select the same role the old instance used (check old instance: EC2 Console → select instance → Security tab → IAM Role). If you forget, you can attach it later: EC2 → select instance → **Actions → Security → Modify IAM role**.
7. **Allocate + Associate EIP:**
   - EC2 → Network & Security → **Elastic IPs** → **Allocate Elastic IP address** → Allocate
   - Select the newly allocated EIP → **Actions → Associate Elastic IP address**
   - Resource type: **Instance** | Instance: select your new t3.large | Private IP: leave default → **Associate**
   - Verify: the instance's **Public IPv4 address** in EC2 console should now show the EIP
   > **Note — EIPs are region-specific and cannot be transferred.** Your old EIP
   > (`44.245.29.65`, us-west-2) stays there until you release it in Phase G. You
   > will receive a **brand new IP address** in us-east-1. Update `~/.ssh/config`,
   > bookmarks, and `infra_local.md` with this new IP (Phase E covers this).

### Phase D — First-boot verification (SSH into new instance)

> **t3.large RAM budget** — verify headroom before declaring success:
>
> | Component | K8s limit | Notes |
> |-----------|-----------|-------|
> | Flask/Dash | 512 Mi | `dashboard/manifests/pod-flask.yaml` |
> | Airflow webserver | 1 Gi | `airflow/helm/values.yaml` |
> | Airflow scheduler | 1 Gi | `airflow/helm/values.yaml` |
> | Airflow triggerer | 256 Mi | `airflow/helm/values.yaml` |
> | Airflow dag-processor | 512 Mi | `airflow/helm/values.yaml` |
> | K3s system (host) | ~500 Mi | not a K8s pod |
> | MariaDB (host) | ~500 Mi | not a K8s pod |
> | **Worst-case total** | **~4.75 Gi** | **~3.25 Gi free on 8 GB** |
>
> If `free -h` shows > 6 GB used under load, stop here and resize to t3.xlarge before continuing.

```bash
ssh -i .../kafkaProjectKeyPair_4-29-2025.pem ec2-user@52.70.211.1

sudo systemctl status k3s
kubectl get pods --all-namespaces          # wait 3–5 min for pods to start
sudo systemctl status mariadb
kubectl get secret db-credentials -n airflow-my-namespace

# Get new private IP — will differ from old 172.31.23.236
ip addr show | grep "inet 172"
```

**Critical — update db-credentials secret** with new private IP (old one is baked in from AMI):

```bash
NEW_IP=$(hostname -I | awk '{print $1}')
for NS in airflow-my-namespace default; do
  kubectl create secret generic db-credentials -n $NS \
    --from-literal=DB_USER=airflow_user \
    --from-literal=DB_PASSWORD=<password> \
    --from-literal=DB_HOST=$NEW_IP \
    --from-literal=DB_NAME=database_one \
    --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
    --dry-run=client -o yaml | kubectl apply -f -
done

# Restart pods to pick up the new secret
kubectl rollout restart deployment -n airflow-my-namespace
kubectl delete pod my-kuber-pod-flask -n default
```

**Verify resource limits are active** (limits are defined in manifests/values.yaml and protect t3.large from OOMKill cascades — OOMKill = Out Of Memory Kill, where the OS force-kills a pod that exceeds its RAM limit):

```bash
# Confirm Flask pod has memory limit of 512Mi
kubectl describe pod my-kuber-pod-flask -n default | grep -A6 "Limits:"

# Confirm Airflow scheduler has memory limit of 1Gi
kubectl describe pod -n airflow-my-namespace -l component=scheduler | grep -A6 "Limits:"
```

### Phase E — Update local config files

**`~/.ssh/config`** — update the `ec2-stock` entry on your Mac:

```
Host ec2-stock
    HostName 52.70.211.1
    User ec2-user
    IdentityFile ~/Documents/Programming/Python/Data-Pipeline-2026/kafkaProjectKeyPair_4-29-2025.pem
```

**`.env.deploy`** — update both values to us-east-1 (deploy.sh reads these for ECR auth and image push):

```bash
ECR_REGISTRY="683010036255.dkr.ecr.us-east-1.amazonaws.com"
AWS_REGION="us-east-1"
```

| File | Change |
|------|--------|
| `~/.ssh/config` | `HostName` → `52.70.211.1` (see snippet above) |
| `.env.deploy` | `ECR_REGISTRY` → us-east-1 registry; `AWS_REGION` → `us-east-1` (see snippet above) |
| `infra_local.md` | Update EIP, MariaDB private IP (from Phase D), service URLs |

### Phase F — First deploy + testing

```bash
# Run deploy to push image to new ECR and refresh K8s secrets
./scripts/deploy.sh

# Then test via SSH tunnel:
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

**Pre-deploy checklist (before running `./scripts/deploy.sh`):**
- [ ] IAM role attached to new instance (EC2 Console → select instance → Security tab → IAM Role must be non-empty). Without this, deploy fails at Step 4 with "Unable to locate credentials" — `aws ecr get-login-password` requires the instance's IAM role to get a temporary ECR token.
- [ ] Verify: `ssh ec2-stock 'aws sts get-caller-identity'` — should return your account ID, not an error

**Post-deploy checklist:**
- [ ] All pods Running (`kubectl get pods --all-namespaces`)
- [ ] Airflow UI loads at `http://localhost:30080`
- [ ] Manually trigger both DAGs — all tasks succeed
- [ ] Dashboard shows data at `http://localhost:32147/dashboard/`
- [ ] `free -h` shows < 6 GB used (t3.large headroom check)

### Phase G — Cleanup (after 48–72 hours stable)

```bash
# Release old EIP (stops billing for idle EIP)
# AWS Console (us-west-2) → Elastic IPs → Disassociate → Release

# Stop (don't terminate) old instance — keep as safety net for 1 week
# After 1 week: Terminate instance, delete old AMI + EBS snapshots
# Delete us-west-2 ECR repo to stop paying for stored images
```

**Success criteria:** Both DAGs run clean, dashboard displays data, deploy.sh completes without errors, `free -h` < 6 GB used.

---

## 14. Set Up and Activate Snowflake

**When:** First-time Snowflake setup, or re-establishing the Snowflake connection after a migration.

**Prerequisites:**
- Snowflake account in AWS us-east-1 (see sign-up steps below)
- EC2 instance running and accessible via `ssh ec2-stock`
- `./scripts/deploy.sh` working

### Phase A — Sign up for Snowflake

1. Go to `app.snowflake.com` → Start for free
2. Cloud: **AWS**, Region: **US East (N. Virginia)** — matches EC2 region
3. Edition: **Standard** (free trial gives $400 credits)
4. Note your account identifier — format `abc12345.us-east-1` — needed for all connections

### Phase B — Initial SQL setup (run in Snowsight worksheet)

```sql
-- Warehouse: X-Small, auto-suspends after 60s idle to minimize cost
CREATE WAREHOUSE IF NOT EXISTS PIPELINE_WH
  WAREHOUSE_SIZE = 'X-SMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE;

CREATE DATABASE IF NOT EXISTS PIPELINE_DB;
CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.RAW;

-- Least-privilege service role for the pipeline
CREATE ROLE IF NOT EXISTS PIPELINE_ROLE;
GRANT USAGE ON WAREHOUSE PIPELINE_WH TO ROLE PIPELINE_ROLE;
GRANT USAGE ON DATABASE PIPELINE_DB TO ROLE PIPELINE_ROLE;
GRANT USAGE ON SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON ALL TABLES IN SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON FUTURE TABLES IN SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;

-- Service user — store the password in .env / K8s secret, never in source code
CREATE USER IF NOT EXISTS PIPELINE_USER
  PASSWORD = '<STRONG_PASSWORD>'
  DEFAULT_ROLE = PIPELINE_ROLE
  DEFAULT_WAREHOUSE = PIPELINE_WH
  DEFAULT_NAMESPACE = 'PIPELINE_DB.RAW';

GRANT ROLE PIPELINE_ROLE TO USER PIPELINE_USER;
```

### Phase C — Store credentials

**Local `.env`:**
```
SNOWFLAKE_ACCOUNT=<account_identifier>
SNOWFLAKE_USER=PIPELINE_USER
SNOWFLAKE_PASSWORD=<strong_password>
SNOWFLAKE_DATABASE=PIPELINE_DB
SNOWFLAKE_SCHEMA=RAW
SNOWFLAKE_WAREHOUSE=PIPELINE_WH
```

**K8s secret on EC2** (both namespaces):
```bash
ssh ec2-stock
for NS in airflow-my-namespace default; do
  kubectl create secret generic snowflake-credentials -n $NS \
    --from-literal=SNOWFLAKE_ACCOUNT=<account_identifier> \
    --from-literal=SNOWFLAKE_USER=PIPELINE_USER \
    --from-literal=SNOWFLAKE_PASSWORD=<password> \
    --from-literal=SNOWFLAKE_DATABASE=PIPELINE_DB \
    --from-literal=SNOWFLAKE_SCHEMA=RAW \
    --from-literal=SNOWFLAKE_WAREHOUSE=PIPELINE_WH \
    --dry-run=client -o yaml | kubectl apply -f -
done
```

**Helm values** — add `snowflake-credentials` to `extraEnvFrom` in `airflow/helm/values.yaml`:
```yaml
extraEnvFrom: |
  - secretRef:
      name: db-credentials
  - secretRef:
      name: snowflake-credentials
```

**Pod manifest** — add to `envFrom` in `dashboard/manifests/pod-flask.yaml`:
```yaml
envFrom:
- secretRef:
    name: db-credentials
- secretRef:
    name: snowflake-credentials
```

### Phase D — Register Airflow Connection

Airflow UI → Admin → Connections → Add:
- **Conn Id:** `snowflake_default`
- **Conn Type:** Snowflake
- **Account:** your account identifier
- **Login:** `PIPELINE_USER`
- **Password:** your password
- **Schema:** `RAW`
- **Extra (JSON):** `{"warehouse": "PIPELINE_WH", "database": "PIPELINE_DB", "role": "PIPELINE_ROLE"}`

### Phase E — Deploy and verify

```bash
./scripts/deploy.sh
```

Then manually trigger both DAGs and verify in Snowsight:
```sql
SELECT COUNT(*) FROM PIPELINE_DB.RAW.COMPANY_FINANCIALS;
SELECT COUNT(*) FROM PIPELINE_DB.RAW.WEATHER_HOURLY;
-- Both should return > 0 rows
```

Also check Airflow task logs for: `Loaded N rows into Snowflake COMPANY_FINANCIALS`

### Phase F — Cut dashboard over to Snowflake (after validating data)

Once Snowflake data looks correct, update the K8s secret to switch the dashboard engine:
```bash
ssh ec2-stock
kubectl create secret generic snowflake-credentials -n default \
  ... (existing values) \
  --from-literal=DB_BACKEND=snowflake \   # this key activates the Snowflake engine in app.py
  --dry-run=client -o yaml | kubectl apply -f -

kubectl delete pod my-kuber-pod-flask -n default   # restart to pick up new value
```

**Success criteria:** Rows appear in Snowsight after DAG runs; dashboard loads data correctly when `DB_BACKEND=snowflake`.

---

**Last updated:** 2026-04-04
