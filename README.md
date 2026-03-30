# stock_live_data

End-to-end data pipeline that pulls daily stock prices (AAPL, MSFT, GOOGL) and hourly weather data from public APIs, stores them in MariaDB, and serves an interactive Plotly/Dash dashboard — orchestrated by Apache Airflow and hosted on AWS EC2 via K3S Kubernetes.

See **[OVERVIEW.md](OVERVIEW.md)** for full setup, deployment, and architecture details.

---

## Architecture

```
AWS EC2 t3.xlarge
└── K3S Kubernetes
    │
    ├── Pod 1: Apache Airflow 3.1.8 (Helm, LocalExecutor)
    │     DAG: dag_stocks    — Alpha Vantage daily OHLCV → MariaDB
    │     DAG: dag_weather   — Open-Meteo hourly temp  → MariaDB
    │
    ├── Pod 2: Flask + Dash (NodePort 32147)
    │     /dashboard/ — candlestick chart, volume, stats table
    │     /health     — Kubernetes liveness probe
    │
    └── MariaDB (EC2, outside K8s)
          ├── stock_daily_prices
          └── weather_hourly
```

**Step 1 (current):** MariaDB + Airflow + Flask/Dash on EC2/K3S.

**Step 2 (planned):** Replace MariaDB with Snowflake; add a Kafka streaming layer between Airflow and the database.
