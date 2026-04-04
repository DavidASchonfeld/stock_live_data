# System Architecture Guide

## Overview

Your stock data pipeline is a production system running on a single AWS EC2 instance using **K3S** (lightweight Kubernetes) to orchestrate multiple containerized services. This guide explains the architecture, component relationships, and data flow.

**Quick Navigation**
- Want to understand what K3S is? See [K3S Section](#why-k3s)
- Curious about Docker vs containerd? See [Container Runtime Section](#container-runtime-docker-vs-containerd)
- Looking for technical term definitions? See [GLOSSARY.md](GLOSSARY.md)
- Need to understand your data pipeline? See [ETL Data Flow Section](#etl-data-flow-extract-transform-load)

---

## Why K3S?

**K3S** is a lightweight, certified Kubernetes distribution designed for resource-constrained environments. Instead of deploying full Kubernetes (which requires 4+ GB RAM and multiple nodes), K3S bundles everything into a single binary (~40 MB).

### Your Use Case: K3S on EC2

You run K3S on a single **t3.xlarge EC2 instance** (4 vCPU, 16 GB RAM) in AWS. This choice saves money compared to alternatives:

- **Full EKS (Elastic Kubernetes Service):** $0.10/hour cluster fee + compute costs → ~$100+/month just for the cluster
- **K3S on EC2:** Only pay for the t3.xlarge instance (~$0.15/hour) → ~$110/month total, with full Kubernetes features
- **Docker Compose on EC2:** No orchestration, no auto-restart, harder to scale → risky for production

K3S gives you production-grade container orchestration (auto-restart, rolling updates, health checks) at a fraction of the cost.

---

## Container Runtime: Docker vs containerd

### What is a Container Runtime?

A **container runtime** is software that executes containers — it manages the process isolation, filesystem mounts, and networking that make containers work. Think of it as the "engine" that runs your Docker images.

### Docker vs containerd

| Aspect | Docker | containerd |
|--------|--------|-----------|
| **What it is** | Full platform (runtime + CLI + extras) | Lightweight container runtime only |
| **Size** | ~100 MB | ~20 MB |
| **Features** | Docker CLI, build tools, networking, volumes, compose | Just runs containers |
| **Kubernetes integration** | Works, but needs extra daemon | Native K8s support, tighter integration |
| **Your setup** | K3S uses containerd by default | Lighter weight, faster on EC2 |

### Why K3S Chose containerd

K3S ships with **containerd** pre-configured because:
1. **Minimal overhead:** No Docker daemon bloat; K3S speaks directly to containerd
2. **Native CRI support:** containerd implements the Kubernetes Container Runtime Interface (CRI)
3. **Faster pod startup:** No extra layer between Kubernetes and container execution
4. **Lower resource usage:** Critical on your single t3.xlarge instance

When you push images to AWS ECR and K3S pulls them, the flow is:
```
Your machine → Docker build/push → AWS ECR (image registry)
    ↓
K3S node → containerd pulls image from ECR → containerd spawns pod
```

Containerd is silent — you don't interact with it directly. You push images, and containerd makes them run.

---

## Kubernetes Components in Your Project

### Pods

A **Pod** is the smallest unit in Kubernetes — one or more containers that share networking and storage. In your project, you run:
- **Airflow scheduler + webserver pod** (runs the DAG scheduler and Airflow UI)
- **MariaDB pod** (database)
- **Flask + Dash pod** (web application & dashboard)

Each pod is a complete isolated environment. If a pod crashes, K3S automatically restarts it.

### Services

A **Service** exposes a pod's ports to the outside world (or to other pods). You define:
- **NodePort services:** Exposes the Airflow UI on port 8080 of your EC2 instance (the "node")
- **ClusterIP services:** Internal networking between pods (e.g., Flask talks to MariaDB)

Without a Service, external traffic cannot reach your pods — Services are the gateway.

### PersistentVolumes (PV) and PersistentVolumeClaims (PVC)

#### PersistentVolume (PV)

A **PV** is a piece of storage allocated on your EC2 host that **survives pod crashes and restarts**.

Think of it as a folder on your EC2 machine that Kubernetes manages:
```
EC2 host (/tmp/airflow-dags, /var/lib/mysql, etc.)
    ↓
Kubernetes PV (allocated + managed)
    ↓
Pod mounts this folder and can read/write files
```

When a pod crashes:
- The pod is gone
- The PV remains on the EC2 filesystem
- K3S restarts the pod
- Pod remounts the same PV → data is preserved

**Example:** Your Airflow DAG files live in a PV. When the Airflow pod restarts, it sees the same DAG files.

#### PersistentVolumeClaim (PVC)

A **PVC** is a "request for storage" — pods don't mount PVs directly. Instead:
1. You define a PVC: "I need 10 GB of storage"
2. K3S matches the PVC to an available PV
3. The pod mounts the PVC (which points to the PV)

This adds a layer of abstraction. Benefits:
- Pods don't care where storage is; they just request what they need
- Storage can be swapped (local disk, NFS, cloud storage) without pod changes
- Multiple pods can share a PVC if configured

**Example YAML flow:**
```yaml
PersistentVolume:
  path: /tmp/airflow-dags          # folder on EC2 host

PersistentVolumeClaim:
  size: 5Gi                        # request 5 GB

Pod:
  volumeMounts:
    - mountPath: /opt/airflow/dags # where to mount inside pod
      name: airflow-dags           # reference to PVC
```

### Namespaces

A **Namespace** is a logical partition in your K3S cluster. You use the `airflow` namespace to keep Airflow pods separate from default system pods. This prevents conflicts and makes cleanup easier.

---

## ETL Data Flow: Extract, Transform, Load

Your system implements a classic **ETL pipeline** (Extract → Transform → Load):

```
                         ┌─────────────────────┐
                         │  External APIs      │
                         │ - SEC EDGAR         │
                         │   (company finan.)  │
                         │ - Open-Meteo        │
                         │   (weather data)    │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   EXTRACT PHASE     │
                         │ stock_client.py     │
                         │ weather_client.py   │
                         │  (API calls)        │
                         └──────────┬──────────┘
                                    │
                  ┌─────────────────▼────────────────┐
                  │      AIRFLOW SCHEDULER           │
                  │  (dag_stocks.py & dag_weather)   │
                  │                                  │
                  │  1. TRANSFORM phase:             │
                  │     - JSON parse (pandas)        │
                  │     - json_normalize() flatten   │
                  │     - Column extraction          │
                  │                                  │
                  │  2. LOAD phase:                  │
                  │     - DataFrame.to_sql()         │
                  │     - Insert into MariaDB        │
                  └─────────────────┬────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │   MariaDB (MySQL)   │
                         │  - financials table  │
                         │  - weather table     │
                         └──────────┬──────────┘
                                    │
                  ┌─────────────────▼────────────────┐
                  │   Flask Web App                  │
                  │  - Reads from MariaDB            │
                  │  - Serves JSON API endpoints     │
                  └─────────────────┬────────────────┘
                                    │
                         ┌──────────▼──────────┐
                         │  Dash Visualization │
                         │  (Browser-based UI) │
                         └─────────────────────┘
```

### Extract Phase

**Location:** `data_pipeline/scripts/stock_client.py` and `weather_client.py`

Your extraction scripts call external APIs:
- **stock_client.py → edgar_client.py:** Calls SEC EDGAR XBRL API (`GET https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json`)
- **weather_client.py:** Calls Open-Meteo API (`GET https://api.open-meteo.com/v1/forecast?...`)

Result: Raw JSON responses from APIs.

### Transform Phase

**Location:** `data_pipeline/airflow/dags/dag_stocks.py` and `dag_weather.py`

Airflow DAG tasks receive the raw JSON and:

1. **Parse JSON** → Convert from text to Python dictionaries
2. **Normalize with `pandas.json_normalize()`** → Flatten nested JSON into flat columns
3. **Extract relevant columns** → Drop unnecessary fields, keep only what you need
4. **Create DataFrame** → Organize data into a table-like structure

**Example:**
```python
# Raw JSON from SEC EDGAR (nested XBRL structure)
raw_json = {
    "cik": 320193,
    "entityName": "Apple Inc.",
    "facts": {
        "us-gaap": {
            "NetIncomeLoss": {
                "units": { "USD": [{"end": "2023-09-30", "val": 96995000000, ...}] }
            }
        }
    }
}

# After flatten_company_financials() transformation
df = pd.DataFrame({
    "ticker": ["AAPL", "AAPL"],
    "metric": ["NetIncomeLoss", "Assets"],
    "period_end": ["2023-09-30", "2023-09-30"],
    "value": [96995000000, 352583000000],
    "form_type": ["10-K", "10-K"],
})
```

### Load Phase

**Location:** Same DAG tasks

The transformed DataFrame is written to MariaDB:
```python
df.to_sql("company_financials", connection, if_exists="replace", index=False)
```

This replaces the `company_financials` table in MariaDB with fresh data from SEC EDGAR. Airflow schedules this to run every 5 minutes (configurable).

---

## How Kubernetes Pods Run Your ETL

### Airflow Pod Lifecycle

1. **K3S pulls Airflow Docker image** from AWS ECR
2. **containerd runs the image** as a pod in the `airflow` namespace
3. **Airflow scheduler starts inside the pod** and reads DAGs from a mounted PVC
4. **Every scheduled interval (e.g., daily at 8 AM),** the scheduler triggers a DAG run
5. **DAG tasks execute** (extract from APIs, transform with pandas, load to MariaDB)
6. **Results stored in MariaDB** (which runs in its own pod)

### Pod Interdependencies

```
Airflow pod → reads DAGs from PVC (/opt/airflow/dags)
           → calls extract scripts (stock_client.py)
           → writes to MariaDB pod via ClusterIP Service

MariaDB pod → listens on ClusterIP service
            → receives write requests from Airflow
            → stores data on mounted PVC (/var/lib/mysql)

Flask pod   → reads from MariaDB via ClusterIP Service
            → exposes API on NodePort Service (port 5000)
            → serves JSON to Dash on the browser
```

---

## Helm: Templating Your Kubernetes Manifests

**Helm** is a package manager for Kubernetes. Instead of writing raw YAML manifests, Helm lets you define **templates** with variables:

```yaml
# Helm template (values.yaml provides variables)
image: {{ .Values.image.repository }}:{{ .Values.image.tag }}
replicas: {{ .Values.replicas }}
```

Benefits:
- **Reusability:** One template, multiple environments (dev, staging, prod)
- **Easier upgrades:** Change `values.yaml`, run `helm upgrade`, and Kubernetes updates your pods
- **Version control:** Keep `values.yaml` in Git; roll back by switching versions

In your project, Helm is used to deploy Airflow with various configurations (image, scheduler replicas, database credentials).

---

## Alerting & Monitoring

The pipeline includes an alerting layer that notifies you when things go wrong or data goes stale.

### How It Works

**Failure/Retry Alerts** — Both data DAGs (`Stock_Market_Pipeline`, `API_Weather-Pull_Data`) have Airflow callbacks (`on_failure_callback`, `on_retry_callback`) that fire when a task fails or retries. These callbacks log to the PVC and send a Slack message via webhook.

**Data Staleness Monitor** — A separate DAG (`Data_Staleness_Monitor`) runs every 30 minutes, queries `MAX(filed_date)` and `MAX(imported_at)` from the data tables, and alerts if data exceeds freshness thresholds (168h for financials, 2h for weather).

**Notification Channels:**
- **Slack webhook** (primary) — Slack is a messaging app (install at slack.com); a "webhook" is a secret URL Slack gives you that, when your pipeline POSTs a message to it, delivers that message to a Slack channel on your phone or Mac. Configured via `SLACK_WEBHOOK_URL` env var / K8s Secret. See [Runbook #12](../operations/RUNBOOKS.md#12-configure-slack-alerting).
- **Log-only fallback** — when no webhook URL is set (the default), alerts are printed to stdout + PVC log files; no external account needed

> **Current status (as of 2026-03-31):** A Slack webhook URL was generated and the alerting code is fully wired up, but it has **not been connected to a Slack account or workspace**. The system is currently in **log-only mode** — no Slack notifications are actively being received.

**Vacation Mode Integration:**
- Failure/retry callbacks **always fire** — if a DAG somehow fails during vacation mode (instead of being cleanly skipped), that indicates vacation mode is broken, which you'd want to know about
- The staleness monitor **respects vacation mode** — calls `check_vacation_mode()` before checking, since stale data is expected when pipelines are paused

### Key Files

| File | Purpose |
|------|---------|
| `airflow/dags/alerting.py` | Core module: callbacks, staleness checker, Slack sender |
| `airflow/dags/alert_config.py` | Configuration: webhook URL, staleness thresholds (gitignored) |
| `airflow/dags/dag_staleness_check.py` | Staleness monitoring DAG (runs every 30 min) |

---

## Summary: How It All Connects

1. **EC2 Instance** runs K3S (lightweight Kubernetes)
2. **K3S pods** run containerd, which executes Docker images pulled from AWS ECR
3. **Airflow pod** schedules your ETL DAGs (extract, transform, load)
4. **Extract scripts** call external APIs (SEC EDGAR XBRL for financials, Open-Meteo for weather)
5. **Transform phase** normalizes JSON with pandas
6. **Load phase** writes DataFrames to MariaDB
7. **MariaDB pod** persists data on a PersistentVolume
8. **Flask pod** reads from MariaDB and exposes a REST API
9. **Dash** in the browser consumes the Flask API and visualizes stock/weather data
10. **PersistentVolumes** ensure data survives pod crashes and restarts
11. **Alerting** notifies you via Slack (or logs) when tasks fail, retry, or data goes stale

All components communicate via **Kubernetes Services** (internal networking) and **NodePorts** (external access to the Airflow UI and Flask API).

---

## Key Concepts Recap

- **K3S:** Lightweight Kubernetes for cost efficiency
- **containerd:** Container runtime; K3S's engine for running images
- **Pod:** Smallest K8s unit; one or more containers
- **Service:** Gateway to reach pods (internal or external)
- **PersistentVolume:** Storage that survives pod crashes
- **PersistentVolumeClaim:** Request for storage; pod mounts a PVC
- **ETL:** Extract (APIs) → Transform (pandas) → Load (MariaDB)
- **Airflow DAG:** Workflow definition; scheduler triggers tasks on a schedule
- **Helm:** Template system for Kubernetes manifests

For more definitions, see [GLOSSARY.md](GLOSSARY.md).
