# stock_live_data

End-to-end data pipeline that pulls daily stock prices (AAPL, MSFT, GOOGL) and hourly weather data from public APIs, stores them in MariaDB, and serves an interactive Plotly/Dash dashboard — orchestrated by Apache Airflow and hosted on AWS EC2 via K3S Kubernetes.

**Quick Navigation:**
- **[OVERVIEW.md](OVERVIEW.md)** — Full setup, deployment, and production status
- **[docs/INDEX.md](docs/INDEX.md)** — Complete documentation index (start here if overwhelmed)
- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — Why K3S? How Kubernetes/Docker/ETL work?
- **[docs/GLOSSARY.md](docs/GLOSSARY.md)** — Define technical terms (SMA, PV, PVC, DAG, etc.)
- **[docs/DEBUGGING.md](docs/DEBUGGING.md)** — Troubleshooting guide

---

## Architecture

```
AWS EC2 t3.xlarge
└── K3S Kubernetes
    │
    ├── Pod 1: Apache Airflow 3.1.8 (Helm, LocalExecutor)
    │     DAG: dag_stocks    — SEC EDGAR XBRL financials → MariaDB
    │     DAG: dag_weather   — Open-Meteo hourly temp    → MariaDB
    │
    ├── Pod 2: Flask + Dash (NodePort 32147)
    │     /dashboard/ — candlestick chart, volume, stats table
    │     /health     — Kubernetes liveness probe
    │
    └── MariaDB (EC2, outside K8s)
          ├── company_financials
          └── weather_hourly
```

**Step 1 (current):** MariaDB + Airflow + Flask/Dash on EC2/K3S.

**Step 2 (planned):** Replace MariaDB with Snowflake; add a Kafka streaming layer between Airflow and the database.
