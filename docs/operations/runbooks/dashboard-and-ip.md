# Runbooks 7–10: Dashboard, IP, Stale Data, New Sources

> Part of the [Runbooks Index](../RUNBOOKS.md).

---

## 7. Update Flask Dashboard Image

**When:** You've changed Flask/Dash code and need to deploy a new image.

The simplest approach: just run `./scripts/deploy.sh` — it handles build, push, and restart automatically.

For manual steps (if deploy.sh isn't available):

```bash
# 1. Build, tag, and push to ECR
cd dashboard
docker build -t stock-dashboard:latest .
docker tag stock-dashboard:latest <ECR_URI>/stock-dashboard:latest
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <ECR_URI>
docker push <ECR_URI>/stock-dashboard:latest

# 2. Restart Flask pod
ssh ec2-stock kubectl delete pod my-kuber-pod-flask -n default

# 3. Verify — http://localhost:32147/dashboard/ via SSH tunnel
sleep 30
ssh ec2-stock kubectl get pods -n default
```

**Success criteria:** Flask pod Running with new image, dashboard shows expected changes.

---

## 8. Change Working Location (IP Update)

**When:** You're working from a new network and can't SSH into EC2.

```bash
# 1. Find your current public IP
curl ifconfig.me

# 2. AWS Console → EC2 → Security Groups → edit SSH rule (port 22) → update source IP

# 3. Test SSH
ssh ec2-stock

# 4. Re-establish SSH tunnel
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock

# 5. Update infra_local.md (gitignored) with the new IP
```

**Success criteria:** SSH connects, Airflow UI and dashboard accessible via tunnel.

---

## 9. Investigate Stale Data

**When:** Dashboard is showing old data and you need to find out why.

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
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list

# 3. Check recent DAG runs for failures
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags list-runs Stock_Market_Pipeline

# 4. If runs are failing, check task logs in the Airflow UI

# 5. Common causes:
#    - DAG paused → unpause it
#    - API rate limited → wait or check key
#    - DB credentials expired → Runbook #3
#    - Scheduler pod not running → check pod status

# 6. Once fixed, backfill if needed (Runbook #6)
```

---

## 10. Add a New API Data Source

**When:** Adding a new external API (e.g., crypto prices, news sentiment).

**Design steps before writing code:**

1. **API Research** — Rate limit? Authentication? Response format? Data freshness?
2. **Database Design** — Columns? Primary key? Join keys to existing tables?
3. **DAG Design** — Schedule interval? Retry policy? Dependencies?
4. **Implementation** — follow [Add a New DAG](deploy-and-dag.md) runbook
   - Create client script (API wrapper)
   - Create DAG file (extract → transform → load)
   - Add validation at each gate
   - Test locally, deploy, verify
5. **Dashboard Integration** — Add Flask endpoint + Dash visualization, update image (Runbook #7)
