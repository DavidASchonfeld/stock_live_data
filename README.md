# data_pipeline

> **Status: In Progress — Step 1 of 2**
> Step 1 (current): Airflow + MariaDB + Flask/Dash on AWS EC2/K3S — fully operational.
> Step 2 (planned): Migrate storage to Snowflake, add Kafka streaming layer.

End-to-end data pipeline that pulls daily stock financials (AAPL, MSFT, GOOGL) from SEC EDGAR and hourly weather data from Open-Meteo, stores them in MariaDB, and serves an interactive Plotly/Dash dashboard — orchestrated by Apache Airflow and hosted on AWS EC2 via K3S Kubernetes.

---

## What It Does

Two Airflow DAGs run on a schedule:
1. **Stock pipeline** — resolves ticker → CIK, fetches XBRL financial data from SEC EDGAR, transforms it, and loads it into `company_financials` in MariaDB.
2. **Weather pipeline** — fetches 7-day hourly forecasts from Open-Meteo and appends them to `weather_hourly` in MariaDB.

A Flask + Dash web app queries MariaDB and renders an interactive candlestick chart with volume bars and a summary stats table. A third DAG runs every 30 minutes to check data freshness and alert if pipelines go stale.

---

## Architecture

```
Your Mac
└── docker build/push → AWS ECR (image registry)

AWS EC2 t3.large (2 vCPU, 8 GB RAM, 100 GiB EBS)
├── MariaDB  ← runs directly on EC2 (not in K8s)
│   └── database_one
│       ├── company_financials  (ticker, metric, value, date, ...)
│       └── weather_hourly      (time, temperature_2m, lat/lon, ...)
│
└── K3S Kubernetes
    ├── Pod: Apache Airflow 3.1.8  (Helm chart, LocalExecutor)
    │   ├── dag_stocks.py           SEC EDGAR XBRL → MariaDB  (every 5 min)
    │   ├── dag_weather.py          Open-Meteo    → MariaDB  (every 5 min)
    │   └── dag_staleness_check.py  freshness monitor         (every 30 min)
    │
    ├── Pod: Flask + Dash  (Gunicorn, NodePort 32147)
    │   ├── /dashboard/   candlestick chart, volume, stats
    │   └── /health       Kubernetes liveness probe
    │
    ├── Pod: PostgreSQL   (Airflow metadata DB — not your data)
    │
    └── PersistentVolumes (hostPath on EC2 disk)
        ├── DAG files     /home/ubuntu/airflow/dags
        ├── Airflow logs  /home/ubuntu/airflow_logs
        └── Task logs     /home/ubuntu/airflow/out
```

**Data flow:** API → `extract()` → `transform()` (pandas) → `load()` (SQLAlchemy → MariaDB) → Flask/Dash → browser.

**Why K3S?** Full Kubernetes features (auto-restart, rolling updates, health probes) at ~$110/month for a single EC2 instance, vs. $100+/month just for an EKS cluster fee.

---

## Tech Stack

| Layer | Technology | Notes |
|---|---|---|
| Language | Python 3.12 | |
| Orchestration | Apache Airflow 3.1.8 | TaskFlow API, LocalExecutor, Helm 1.20.0 |
| Web / Dashboard | Flask 2.3.3 + Dash 2.17.1 + Plotly 5.22.0 | Gunicorn 22.0.0, 2 workers |
| Data processing | Pandas 2.2.2 | |
| Database | MariaDB (MySQL-compatible) | runs on EC2 outside K8s |
| DB driver | SQLAlchemy 2.0.30 + pymysql 1.1.1 | |
| Container runtime | containerd (via K3S) | images built with Docker on Mac, stored in ECR |
| Orchestration platform | K3S (lightweight Kubernetes) | single-node, AWS EC2 |
| Image registry | AWS ECR | private, pulled by K3S at deploy time |
| Stock data | SEC EDGAR XBRL API | free, no API key required |
| Weather data | Open-Meteo API | free, no API key required |
| Secrets | K8s Secrets + `.env` files | never hardcoded or committed |

---

## Project Structure

```
data_pipeline/
├── airflow/
│   ├── dags/                   DAG definitions + support modules (mounted into pods via PVC)
│   │   ├── dag_stocks.py         Main stock pipeline (SEC EDGAR → MariaDB)
│   │   ├── dag_weather.py        Weather pipeline (Open-Meteo → MariaDB)
│   │   ├── dag_staleness_check.py  Data freshness monitor + alerting
│   │   ├── edgar_client.py       SEC EDGAR API client (rate limiter, CIK lookup, XBRL parser)
│   │   ├── weather_client.py     Open-Meteo API client
│   │   ├── alerting.py           Task callbacks + Slack/log-only notifications
│   │   ├── dag_utils.py          Shared utilities (vacation mode guard)
│   │   ├── db_config.py          DB credentials from env vars (NOT in git)
│   │   └── file_logger.py        OutputTextWriter: logs to PVC + stdout
│   ├── helm/
│   │   └── values.yaml           Active Helm values for Airflow deployment
│   └── manifests/                K8s PV/PVC/Service YAML files
│
├── dashboard/
│   ├── app.py                  Flask + Dash application
│   ├── Dockerfile              Builds my-flask-app:latest (python:3.12-slim)
│   ├── requirements.txt        Dashboard Python dependencies
│   └── manifests/              Pod and Service YAML for the Flask app
│
├── docs/                       In-depth documentation (see docs/INDEX.md)
│   ├── INDEX.md                  Navigation hub — start here
│   ├── PLAIN_ENGLISH_GUIDE.md    Non-technical project explanation
│   ├── architecture/             System design, data flow, failure modes
│   ├── operations/               Runbooks, debugging, troubleshooting
│   ├── infrastructure/           K3S, PV/PVC, ECR setup details
│   ├── reference/                Glossary, command reference, kubectl cheatsheet
│   └── incidents/                Historical incident logs and root cause analyses
│
├── scripts/
│   └── deploy.sh               One-command deploy (validate → sync → build → push → restart)
│
├── .env.deploy.example         Template for AWS/deploy secrets (copy → .env.deploy, fill in)
└── README.md                   This file
```

---

## Quick Start (Local Development)

### 1. Set up MariaDB

```bash
brew install mariadb && brew services start mariadb
sudo mysql -u root
```

```sql
CREATE DATABASE database_one;
CREATE USER 'airflow_user'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'localhost';
FLUSH PRIVILEGES;
```

### 2. Configure credentials

```bash
cp .env.deploy.example .env
# Edit .env — set DB_HOST=localhost, DB_USER, DB_PASSWORD, DB_NAME=database_one
```

You'll also need `airflow/dags/db_config.py` and `airflow/dags/constants.py` (not in git — see `.env.deploy.example` for the expected variables).

### 3. Set up Python environment

```bash
python3 -m venv airflow_env
source airflow_env/bin/activate
pip install apache-airflow==3.1.8
pip install -r dashboard/requirements.txt
```

### 4. Run Airflow

```bash
export AIRFLOW_HOME=$(pwd)/airflow
airflow db init
airflow scheduler &
airflow webserver   # http://localhost:8080
```

Enable `dag_stocks` and `dag_weather` in the Airflow UI.

### 5. Run the dashboard

```bash
cd dashboard
flask run   # http://localhost:5000/dashboard/
```

---

## Production Deployment (AWS EC2 + K3S)

### Prerequisites
- EC2 instance running with K3S installed
- AWS ECR repository (`my-flask-app`)
- K8s Secret `db-credentials` applied in the `airflow-my-namespace` namespace
- SSH access configured (see `docs/infrastructure/`)

### Deploy

```bash
cp .env.deploy.example .env.deploy
# Fill in: ECR_REGISTRY, AWS_REGION

./scripts/deploy.sh
```

The script validates DAG syntax, rsyncs DAG files to EC2, rebuilds and pushes the Flask Docker image to ECR, applies K8s manifests, and restarts pods.

### Access

| Service | URL |
|---|---|
| Dashboard | `http://<EC2_IP>:32147/dashboard/` |
| Airflow UI | `http://<EC2_IP>:30080/` |

---

## Key Features

- **Validation gates** at every ETL stage — extract (HTTP/JSON), transform (schema/types/ranges), load (pre-insert + post-insert row count), serve (freshness indicators).
- **Alerting layer** — task failure/retry/recovery callbacks; data staleness monitor (30 min). Slack webhook supported; falls back to log-only mode if no webhook is configured. *Note: webhook is wired but not yet connected to a Slack workspace.*
- **Vacation mode** — set Airflow Variable `VACATION_MODE=true` to pause all pipelines without deleting DAGs. Staleness monitor respects this flag.
- **Rate limiting** — SEC EDGAR client uses a token-bucket limiter (8 req/sec, thread-safe) to stay within the 10 req/sec policy.
- **PVC-backed task logs** — `OutputTextWriter` writes structured logs to both stdout and a PVC-mounted path on EC2, surviving pod restarts.
- **Secrets management** — credentials injected via K8s Secrets (production) or `.env` files (local). Nothing sensitive is hardcoded or committed.
- **TaskFlow API** — DAGs use `@dag`/`@task` decorators; data passes between tasks as JSON-serializable return values via XCom.

---

## Roadmap

| Step | Status | Description |
|---|---|---|
| Step 1 | **In progress** | Airflow + MariaDB + Flask/Dash on EC2/K3S |
| Step 2 | Planned | Replace MariaDB with Snowflake; add Kafka streaming layer between Airflow and the database |

---

## Documentation

Full documentation lives in `docs/`. Start at **[docs/INDEX.md](docs/INDEX.md)**.

| Doc | What's in it |
|---|---|
| [docs/PLAIN_ENGLISH_GUIDE.md](docs/PLAIN_ENGLISH_GUIDE.md) | Non-technical overview of the whole system |
| [docs/architecture/SYSTEM_OVERVIEW.md](docs/architecture/SYSTEM_OVERVIEW.md) | Deep dive: K3S, pods, services, ETL |
| [docs/architecture/DATA_FLOW.md](docs/architecture/DATA_FLOW.md) | Validation gates at each pipeline stage |
| [docs/architecture/FAILURE_MODE_MAP.md](docs/architecture/FAILURE_MODE_MAP.md) | Top failure scenarios per component |
| [docs/operations/RUNBOOKS.md](docs/operations/RUNBOOKS.md) | Step-by-step playbooks: deploy, debug, recover |
| [docs/operations/DEBUGGING.md](docs/operations/DEBUGGING.md) | Systematic debugging approach |
| [docs/reference/GLOSSARY.md](docs/reference/GLOSSARY.md) | K3S, PV, XCom, DAG, ETL, and more |
| [docs/reference/COMMANDS.md](docs/reference/COMMANDS.md) | Shell and kubectl command reference |
| [docs/incidents/](docs/incidents/) | Historical incident logs and root cause analyses |
