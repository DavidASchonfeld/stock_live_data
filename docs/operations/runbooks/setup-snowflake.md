# Runbook 14: Set Up and Activate Snowflake

> Part of the [Runbooks Index](../RUNBOOKS.md).

**When:** First-time Snowflake setup, or re-establishing the connection after a migration.

**Prerequisites:** EC2 running, `deploy.sh` working.

---

### Phase A — Sign up for Snowflake

1. Go to `app.snowflake.com` → Start for free
2. Cloud: **AWS**, Region: **US East (N. Virginia)** — matches EC2 region
3. Edition: **Standard** (free trial gives $400 credits)
4. Note your account identifier (format: `abc12345.us-east-1`)

### Phase B — Initial SQL setup (run in Snowsight worksheet)

```sql
-- X-Small warehouse, auto-suspends after 60s idle
CREATE WAREHOUSE IF NOT EXISTS PIPELINE_WH
  WAREHOUSE_SIZE = 'X-SMALL' AUTO_SUSPEND = 60 AUTO_RESUME = TRUE;

CREATE DATABASE IF NOT EXISTS PIPELINE_DB;
CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.RAW;

-- Least-privilege service role
CREATE ROLE IF NOT EXISTS PIPELINE_ROLE;
GRANT USAGE ON WAREHOUSE PIPELINE_WH TO ROLE PIPELINE_ROLE;
GRANT USAGE ON DATABASE PIPELINE_DB TO ROLE PIPELINE_ROLE;
GRANT USAGE ON SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON ALL TABLES IN SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON FUTURE TABLES IN SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;

-- Service user — store password in .env / K8s secret, never in code
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
SNOWFLAKE_PASSWORD=<password>
SNOWFLAKE_DATABASE=PIPELINE_DB
SNOWFLAKE_SCHEMA=RAW
SNOWFLAKE_WAREHOUSE=PIPELINE_WH
```

**K8s secret (both namespaces):**
```bash
ssh ec2-stock
for NS in airflow-my-namespace default; do
  kubectl create secret generic snowflake-credentials -n $NS \
    --from-literal=SNOWFLAKE_ACCOUNT=<account_id> \
    --from-literal=SNOWFLAKE_USER=PIPELINE_USER \
    --from-literal=SNOWFLAKE_PASSWORD=<password> \
    --from-literal=SNOWFLAKE_DATABASE=PIPELINE_DB \
    --from-literal=SNOWFLAKE_SCHEMA=RAW \
    --from-literal=SNOWFLAKE_WAREHOUSE=PIPELINE_WH \
    --dry-run=client -o yaml | kubectl apply -f -
done
```

**Helm values** — add `snowflake-credentials` to `extraEnvFrom` in `values.yaml`:
```yaml
extraEnvFrom: |
  - secretRef:
      name: db-credentials
  - secretRef:
      name: snowflake-credentials
```

Then run `helm upgrade` to apply.

**Pod manifest** — add `snowflake-credentials` to `envFrom` in `dashboard/manifests/pod-flask.yaml`.

### Phase D — Register Airflow Connection

Airflow UI → Admin → Connections → Add:
- **Conn Id:** `snowflake_default`
- **Conn Type:** Snowflake
- **Account / Login / Password / Schema:** your values
- **Extra:** `{"warehouse": "PIPELINE_WH", "database": "PIPELINE_DB", "role": "PIPELINE_ROLE"}`

### Phase E — Deploy and verify

```bash
./scripts/deploy.sh
```

Trigger both DAGs, then verify in Snowsight:
```sql
SELECT COUNT(*) FROM PIPELINE_DB.RAW.COMPANY_FINANCIALS;
SELECT COUNT(*) FROM PIPELINE_DB.RAW.WEATHER_HOURLY;
```

### Phase F — Cut dashboard over

Update K8s secret to switch the dashboard engine:
```bash
kubectl create secret generic snowflake-credentials -n default \
  ... --from-literal=DB_BACKEND=snowflake \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl delete pod my-kuber-pod-flask -n default
```

**Success criteria:** Rows appear in Snowsight after DAG runs; dashboard loads with `DB_BACKEND=snowflake`.
