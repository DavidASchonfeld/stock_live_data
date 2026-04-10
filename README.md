# data_pipeline

> **Status: Step 2 Complete**
> Step 1 ✓ Airflow + MariaDB + Flask/Dash on EC2/K3S — complete.
> Step 2 ✓ Snowflake · dbt · Kafka streaming — complete.

End-to-end data pipeline that pulls daily stock financials (AAPL, MSFT, GOOGL) from SEC EDGAR and hourly weather data from Open-Meteo, streams them through Apache Kafka, stores them in Snowflake, transforms them with dbt, and serves an interactive Plotly/Dash dashboard — orchestrated by Apache Airflow and hosted on AWS EC2 via K3S Kubernetes.

---

## What It Does

Five Airflow DAGs work together in two pairs plus one monitor:

1. **Stock producer** (`dag_stocks.py`, daily) — resolves ticker → CIK, fetches XBRL financial data from SEC EDGAR, cleans it, then publishes a JSON message to the `stocks-financials-raw` Kafka topic.
2. **Stock consumer** (`dag_stocks_consumer.py`, event-driven) — triggered by the producer; reads the message from Kafka, writes new rows to Snowflake `RAW.COMPANY_FINANCIALS`, then runs dbt to build staging views and mart tables.
3. **Weather producer** (`dag_weather.py`, hourly) — fetches a 7-day hourly forecast from Open-Meteo and publishes it to the `weather-hourly-raw` Kafka topic.
4. **Weather consumer** (`dag_weather_consumer.py`, event-driven) — triggered by the producer; reads from Kafka, deduplicates against existing Snowflake rows, writes net-new rows to `RAW.WEATHER_HOURLY`, then runs dbt.
5. **Staleness monitor** (`dag_staleness_check.py`, every 30 min) — checks that both pipelines ran recently and fires a Slack alert if they haven't.

Flask + Dash queries Snowflake's `MARTS` schema (the dbt-built tables) and renders an interactive candlestick chart with volume bars and a stats table.

---

## How Everything Connects (Plain English)

### What each tool does

| Tool | Role in this project |
|---|---|
| **Airflow** | The scheduler and orchestrator. It wakes up on a schedule, runs tasks in order, passes data between them, and triggers other DAGs. Think of it as the conductor of the pipeline. |
| **Kafka** | A message queue that sits between "fetch data" and "store data." The producer DAG drops a message into a Kafka topic (like a mailbox); the consumer DAG picks it up. This decouples the two sides — the producer doesn't need to know or care where the data ends up. |
| **Snowflake** | The cloud data warehouse. It's the permanent home for all cleaned data. Raw API data lands in the `RAW` schema; dbt-built tables live in `STAGING` and `MARTS`. |
| **dbt** | The transformation layer. It takes the raw Snowflake tables and builds clean, deduplicated, tested views and tables on top — all in versioned SQL. The consumer DAGs call dbt automatically after every load. |
| **Flask + Dash** | The web app. It queries Snowflake's mart tables and renders charts in the browser. |
| **K3S / Kubernetes** | Runs all services (Airflow, Kafka, Flask) as containers on EC2. If a pod crashes, Kubernetes restarts it automatically. |

### Data flow, step by step

```
1. Airflow wakes up (daily for stocks, hourly for weather)
        ↓
2. extract() — calls the SEC EDGAR or Open-Meteo API
        ↓
3. transform() — flattens nested JSON into rows, adds audit columns
        ↓
4. publish_to_kafka() — serializes the batch as JSON and drops it
        into a Kafka topic (stocks-financials-raw or weather-hourly-raw)
        ↓
5. trigger_consumer — Airflow fires the consumer DAG
        ↓
6. consume_from_kafka() — reads the message from the Kafka topic
        ↓
7. write_to_snowflake() — loads rows into Snowflake RAW schema
        (daily batch gate skips if already wrote today; weather dedups
        against existing timestamps to avoid hourly duplicates)
        ↓
8. dbt_run — builds STAGING views + MARTS tables on top of RAW
        ↓
9. dbt_test — runs data quality checks (not_null, unique, custom)
        ↓
10. Flask/Dash — queries MARTS and renders the dashboard
```

### Why Kafka in the middle?

Without Kafka: if the Snowflake write fails, the API data is lost and you'd have to re-fetch.  
With Kafka: the message stays in the topic for 48 hours regardless. The consumer can retry without touching the API. The two halves are independently restartable.

---

## Architecture

```
Your Mac
└── ./scripts/deploy.sh → EC2 (rsync DAGs, build image, helm upgrade)

AWS EC2 t3.large (2 vCPU, 8 GB RAM, 100 GiB EBS)
└── K3S Kubernetes
    │
    ├── Pod: Apache Airflow 3.1.8  (Helm chart, LocalExecutor)
    │   ├── dag_stocks.py            SEC EDGAR API → Kafka     (daily)
    │   ├── dag_stocks_consumer.py   Kafka → Snowflake → dbt   (triggered)
    │   ├── dag_weather.py           Open-Meteo API → Kafka    (hourly)
    │   ├── dag_weather_consumer.py  Kafka → Snowflake → dbt   (triggered)
    │   └── dag_staleness_check.py   freshness monitor         (every 30 min)
    │
    ├── Pod: Apache Kafka 4.0  (StatefulSet, KRaft mode, 2Gi PVC)
    │   ├── stocks-financials-raw   (1 partition, 48h/100MB retention)
    │   └── weather-hourly-raw      (1 partition, 48h/100MB retention)
    │
    ├── Pod: Flask + Dash  (Gunicorn, NodePort 32147)
    │   ├── /dashboard/   candlestick chart, volume bars, stats table
    │   └── /health       Kubernetes liveness probe
    │
    ├── Pod: PostgreSQL   (Airflow metadata — not your pipeline data)
    │
    └── PersistentVolumes (hostPath on EC2 disk)
        ├── DAG files     /home/ubuntu/airflow/dags
        ├── Airflow logs  /home/ubuntu/airflow_logs
        └── Kafka data    (2Gi PVC via local-path provisioner)

Snowflake (external cloud warehouse)
    └── PIPELINE_DB
        ├── RAW.COMPANY_FINANCIALS    — stock rows written by consumer DAG
        ├── RAW.WEATHER_HOURLY        — weather rows written by consumer DAG
        ├── STAGING.*                 — dbt VIEWs (zero storage cost)
        └── MARTS.*                   — dbt TABLEs (queried by Flask/Dash)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Orchestration | Apache Airflow 3.1.8 (TaskFlow API, LocalExecutor, Helm 1.20.0) |
| Streaming | Apache Kafka 4.0 (KRaft mode, plain K8s StatefulSet) |
| Data warehouse | Snowflake Standard Edition (XSMALL warehouse, auto-suspend 60s) |
| Transformations | dbt 1.8.0 + dbt-snowflake (models + tests, run by consumer DAGs) |
| Web / Dashboard | Flask 2.3.3 + Dash 2.17.1 + Plotly 5.22.0 |
| Container runtime | containerd (via K3S) |
| Cloud | AWS EC2 t3.large, 100 GiB EBS gp3 (~$70–75/month total) |
| Stock data | SEC EDGAR XBRL API (free, no API key) |
| Weather data | Open-Meteo API (free, no API key) |

---

## Quick Start

**Local dev:** See [OVERVIEW.md](OVERVIEW.md) for full local setup.

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
- **Cost controls** — daily batch gate (write to Snowflake once/day, not every hourly run); weather dedup against existing timestamps; Snowflake XSMALL + auto-suspend 60s; Flask query cache (1hr financials, 15min weather)
- **Vacation mode** — set `VACATION_MODE=true` to pause all pipelines without deleting DAGs
- **Rate limiting** — SEC EDGAR client uses a token-bucket limiter (8 req/sec, thread-safe)
- **PVC-backed task logs** — structured logs survive pod restarts
- **Secrets management** — credentials via K8s Secrets (prod) or `.env` files (local)

---

## Design Decisions

**Kafka: plain StatefulSet over Strimzi Operator**
Kafka runs as a hand-rolled Kubernetes StatefulSet using `apache/kafka:4.0.0` (KRaft mode, no ZooKeeper) rather than the Strimzi operator. Strimzi adds ~200 MB RAM overhead — a real constraint on a t3.large already running K3S, Airflow, Postgres, and Flask. The plain StatefulSet keeps Kubernetes primitives transparent and can be migrated to Strimzi without touching the Kafka client code.

**Kafka topics use hyphen-separated names**
Topic names use hyphens (`stocks-financials-raw`) instead of dots or underscores. Kafka internally converts dots and underscores to underscores in JMX metric names, which causes collision warnings when both are present. Hyphens avoid the issue entirely.

**dbt runs inside the consumer DAG, not on a schedule**
Each consumer DAG calls `dbt run` + `dbt test` after it writes to Snowflake. This means dbt only runs when there's actually new data, which avoids unnecessary Snowflake warehouse spin-ups and keeps the lineage (ingest → transform → test) in one auditable DAG run.

**Large payloads staged to PVC, not XCom**
SEC EDGAR returns ~45 MB of XBRL JSON. Airflow's XCom uses the metadata DB (PostgreSQL), which has a practical size limit. The DAG writes the payload to a shared PVC and passes only the file path (~100 bytes) through XCom.

---

## Roadmap

| Step | Status | Description |
|---|---|---|
| Step 1 | ✓ Complete | Airflow + MariaDB + Flask/Dash on EC2/K3S |
| Step 2 | ✓ Complete | Snowflake · dbt · Kafka streaming layer |
| Step 3 | Planned | Portfolio polish: public dashboard URL, architecture diagram, GitHub Actions CI (dbt test on PR) |
| Step 4 | Planned | MLflow anomaly detection on financial metrics |
| Step 5 | Planned | Terraform (codify EC2 infra as IaC) |

---

## Documentation

Full docs live in `docs/`. Start at **[docs/INDEX.md](docs/INDEX.md)**.

For a non-technical walkthrough, see **[docs/plain-english/](docs/plain-english/)**.
