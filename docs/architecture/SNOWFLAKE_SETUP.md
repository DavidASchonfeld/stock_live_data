# Snowflake Setup — Infrastructure as Code

This document explains how the project's Snowflake infrastructure is defined, how to restore it from scratch, and how each piece fits into the data pipeline.

---

## Why this exists

Terraform handles all AWS resources (EC2, ECR, IAM, security groups). But Snowflake is a separate cloud service — Terraform can't manage it without a Snowflake-specific provider. Instead, this project uses a SQL setup script (`scripts/snowflake_setup.sql`) that captures every object Snowflake needs. If the project is shut down and restarted, running one command restores the entire Snowflake side.

---

## What Snowflake objects this project uses

### Warehouse — `PIPELINE_WH`
A compute warehouse is how Snowflake runs queries. This project uses an **X-SMALL** warehouse, which is the cheapest tier. It auto-suspends after 60 seconds of idle time so you're never billed for it sitting unused overnight.

### Database — `PIPELINE_DB`
One database holds everything. All schemas, tables, and views live inside it.

### Schemas (layers of the data pipeline)

| Schema | Created by | Purpose |
|---|---|---|
| `RAW` | `snowflake_setup.sql` | Raw data landing zone — Airflow DAGs write here |
| `STAGING` | `snowflake_setup.sql` + dbt | dbt views that clean and cast RAW data |
| `MARTS` | `snowflake_setup.sql` + dbt | dbt fact/dim tables — what the dashboard queries |
| `ANALYTICS` | `anomaly_detector.py` at runtime | Anomaly detection results |

**STAGING and MARTS** are pre-created by the setup script so that permissions are in place before dbt runs for the first time. dbt then manages the actual views and tables inside them.

**ANALYTICS** is created automatically by `anomaly_detector.py` the first time the anomaly detection DAG task runs — no manual step needed.

### Role — `PIPELINE_ROLE`
A service role with the minimum permissions needed. All pipeline components (Airflow, dbt, the Flask dashboard, and the anomaly detector) connect as this role. It can read/write RAW, STAGING, and MARTS — nothing else.

### User — `PIPELINE_USER`
The service account used by every pipeline component. Its password is never committed to git; it's injected from `.env.deploy` when you run the setup script, and stored in the K8s secret `snowflake-credentials` for runtime use.

---

## Data flow through Snowflake

```
SEC EDGAR API → Kafka → Airflow DAG
                              │
                              ▼
                    RAW.COMPANY_FINANCIALS       ← written by dag_stocks_consumer.py
                    RAW.WEATHER_HOURLY           ← written by dag_weather_consumer.py
                              │
                              ▼  dbt run
                    STAGING.STG_COMPANY_FINANCIALS  (view — casts types, filters nulls)
                    STAGING.STG_WEATHER_HOURLY       (view — converts epoch timestamps)
                              │
                              ▼  dbt run
                    MARTS.FCT_COMPANY_FINANCIALS  (table — deduplicated annual financials)
                    MARTS.FCT_WEATHER_HOURLY      (table — deduplicated weather by hour)
                    MARTS.DIM_COMPANY             (table — distinct ticker/CIK/name lookup)
                              │
                         ┌────┴────┐
                         ▼         ▼
                   Dashboard   anomaly_detector.py
                                   │
                                   ▼
                    ANALYTICS.FCT_ANOMALIES  (IsolationForest results per company)
```

---

## How to restore Snowflake from scratch

### Step 1 — Add admin credentials to `.env.deploy`

Open `.env.deploy` (copy from `.env.deploy.example` if it doesn't exist) and fill in:

```bash
SNOWFLAKE_ACCOUNT="abc12345.us-east-1"     # your account identifier
SNOWFLAKE_ADMIN_USER="your_login"           # personal Snowflake username with SYSADMIN access
SNOWFLAKE_ADMIN_PASSWORD="your_password"    # personal Snowflake password
SNOWFLAKE_PASSWORD="strong_service_password" # password to set for PIPELINE_USER
```

The admin credentials are only needed for `--snowflake-setup`. The runtime pipeline uses `PIPELINE_USER` credentials stored in the K8s secret `snowflake-secret.yaml`.

### Step 2 — Run the setup command

```bash
./scripts/deploy.sh --snowflake-setup
```

This runs `scripts/snowflake_setup.sql` via Python's `snowflake-connector-python`. Every statement is `CREATE IF NOT EXISTS`, so it's safe to re-run against an account that already has some objects.

You can also combine flags — for a full teardown-and-rebuild:

```bash
./scripts/deploy.sh --snowflake-setup --provision
```

### Step 3 — Verify in the Snowflake web UI

After the script completes, log into Snowflake and check:

- **Warehouses**: `PIPELINE_WH` exists, size X-SMALL, auto-suspend 60s
- **Databases**: `PIPELINE_DB` exists
- **Schemas**: `PIPELINE_DB.RAW`, `PIPELINE_DB.STAGING`, `PIPELINE_DB.MARTS` all exist
- **Roles**: `PIPELINE_ROLE` exists with grants on all three schemas
- **Users**: `PIPELINE_USER` exists, default role = `PIPELINE_ROLE`

### Step 4 — Tables and data populate automatically

Once the pipeline DAGs run, all tables are created automatically:

| When | What gets created |
|---|---|
| First `dag_stocks_consumer` run | `RAW.COMPANY_FINANCIALS` |
| First `dag_weather_consumer` run | `RAW.WEATHER_HOURLY` |
| First `dbt run` (triggered by consumer DAGs) | STAGING views + MARTS tables |
| First anomaly detection run | `ANALYTICS` schema + `ANALYTICS.FCT_ANOMALIES` table |

---

## Credentials and how they're stored

| Location | What's stored | Who uses it |
|---|---|---|
| `.env.deploy` (gitignored) | Admin creds + `SNOWFLAKE_PASSWORD` | `--snowflake-setup` only |
| `airflow/manifests/snowflake-secret.yaml` (gitignored) | `PIPELINE_USER` credentials | K8s secret in both namespaces |
| K8s secret `snowflake-credentials` (in-cluster) | All Snowflake env vars | Airflow pods, Flask pod |
| `profiles.yml` (gitignored) | dbt Snowflake connection | K8s secret `dbt-profiles` |

The deploy script (`scripts/deploy/sync.sh`) also patches two additional values into the secret on every deploy:
- `SNOWFLAKE_ROLE=PIPELINE_ROLE` — read by `anomaly_detector.py`
- `AIRFLOW_CONN_SNOWFLAKE_DEFAULT` — read by Airflow 3 at startup to auto-register the `snowflake_default` connection (so `SnowflakeHook` works on a fresh cluster without any manual UI setup)

---

## Key files

| File | Purpose |
|---|---|
| `scripts/snowflake_setup.sql` | All DDL — warehouse, DB, schemas, role, user |
| `scripts/deploy/snowflake.sh` | Shell module that runs the SQL via Python |
| `scripts/deploy/sync.sh` | Patches `SNOWFLAKE_ROLE` + `AIRFLOW_CONN_SNOWFLAKE_DEFAULT` into K8s secrets |
| `airflow/dags/snowflake_client.py` | Python utility for writing DataFrames to RAW tables |
| `airflow/dags/anomaly_detector.py` | Creates `ANALYTICS` schema + `FCT_ANOMALIES` table at runtime |
| `airflow/dags/dbt/` | dbt project — creates STAGING views + MARTS tables |
| `dashboard/db.py` | Flask dashboard — queries MARTS and ANALYTICS |

---

## What Terraform covers vs. what this covers

| Layer | Tool | Notes |
|---|---|---|
| EC2, ECR, IAM, Elastic IP, Security Group | Terraform (`terraform/`) | AWS infra |
| K8s deployments: Airflow, Kafka, MLflow, Flask | Deploy scripts (`scripts/deploy/`) | K3s on EC2 |
| **Snowflake: warehouse, DB, schemas, role, user** | **This script** (`scripts/snowflake_setup.sql`) | Snowflake infra |
| Snowflake tables and views | Auto-created by DAGs + dbt | On first pipeline run |
| K8s secrets (credentials) | `scripts/deploy/sync.sh` | Applied on every deploy |
