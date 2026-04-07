# Runbooks 5–6: Recover from Outage + Backfill Missing Data

> Part of the [Runbooks Index](../RUNBOOKS.md).

---

## 5. Recover from Total Cluster Outage

**When:** EC2 instance was stopped/restarted, or K3s crashed and nothing is working.

**Steps:**

```bash
# 1. Verify EC2 instance is running in AWS Console
# If stopped: Start it. Note: public IP may change.

# 2. SSH into EC2
ssh ec2-stock
# If timeout: IP changed — update security group and ~/.ssh/config

# 3. Check K3s status
sudo systemctl status k3s
# If not active: sudo systemctl restart k3s — wait 30 seconds

# 4. Check all pods
kubectl get pods --all-namespaces
# PostgreSQL should come up first, then Airflow pods

# 5. If pods stuck in ImagePullBackOff (ECR token expired)
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com

# 6. If pods stuck in Init:0/1 (PostgreSQL not ready)
kubectl get pods -n airflow-my-namespace | grep postgresql
# Wait for postgresql to reach Running — other pods auto-unblock

# 7. Verify services have endpoints (no <none> entries)
kubectl get endpoints -A

# 8. Check PVs are Bound
kubectl get pv,pvc -A

# 9. Verify data integrity
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

# 2a. For Stock data — one trigger fetches all historical data
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger -e '2026-03-30' Stock_Market_Pipeline

# 2b. For Weather data
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger API_Weather-Pull_Data

# 3. Monitor runs — wait for state: success
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list-runs Stock_Market_Pipeline

# 4. Verify data filled — re-run the query from step 1
```

**Success criteria:** Latest data timestamps match expected dates.
