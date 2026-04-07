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
    │   ├── dag_stocks.py           SEC EDGAR XBRL → MariaDB  (daily)
    │   ├── dag_weather.py          Open-Meteo    → MariaDB  (hourly)
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

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Orchestration | Apache Airflow 3.1.8 (TaskFlow API, LocalExecutor, Helm 1.20.0) |
| Web / Dashboard | Flask 2.3.3 + Dash 2.17.1 + Plotly 5.22.0 |
| Database | MariaDB (MySQL-compatible, runs on EC2 outside K8s) |
| Container runtime | containerd (via K3S), images stored in AWS ECR |
| Cloud | AWS EC2 t3.large, 100 GiB EBS gp3 |
| Stock data | SEC EDGAR XBRL API (free, no API key) |
| Weather data | Open-Meteo API (free, no API key) |

---

## Quick Start

**Local dev:** See [OVERVIEW.md](OVERVIEW.md) for full local setup (MariaDB, venv, Airflow, Flask).

**Production deploy:**
```bash
cp .env.deploy.example .env.deploy   # fill in AWS values
./scripts/deploy.sh                  # validates, syncs, builds, restarts
```

**Access (via SSH tunnel):**
```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
# Airflow UI:  http://localhost:30080
# Dashboard:   http://localhost:32147/dashboard/
```

---

## Key Features

- **Validation gates** at every ETL stage — extract, transform, load, and serve
- **Alerting layer** — task failure/retry/recovery callbacks; data staleness monitor (30 min); Slack webhook supported
- **Vacation mode** — set `VACATION_MODE=true` to pause all pipelines without deleting DAGs
- **Rate limiting** — SEC EDGAR client uses a token-bucket limiter (8 req/sec, thread-safe)
- **PVC-backed task logs** — structured logs survive pod restarts
- **Secrets management** — credentials via K8s Secrets (prod) or `.env` files (local)

---

## Roadmap

| Step | Status | Description |
|---|---|---|
| Step 1 | **In progress** | Airflow + MariaDB + Flask/Dash on EC2/K3S |
| Step 2 | Planned | Replace MariaDB with Snowflake; add Kafka streaming layer |

---

## Documentation

Full docs live in `docs/`. Start at **[docs/INDEX.md](docs/INDEX.md)**.

For a non-technical walkthrough, see **[docs/plain-english/](docs/plain-english/)**.
