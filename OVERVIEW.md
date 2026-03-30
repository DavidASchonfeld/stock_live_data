# Stock Live Data — Project Overview

## Project Summary

A production-deployed, end-to-end data pipeline that pulls daily stock prices (AAPL, MSFT, GOOGL) and hourly weather data from public APIs, stores them in a MariaDB database, and serves an interactive Plotly/Dash dashboard — all orchestrated by Apache Airflow and hosted on AWS EC2 via K3S Kubernetes.

This is **Step 1** of a larger learning project. Step 2 will migrate the database to Snowflake and introduce a Kafka streaming layer.

---

## Live Demo

| Service | URL |
|---|---|
| Flask/Dash Dashboard | http://\<YOUR_EC2_IP\>:32147/dashboard/ |
| Airflow UI | http://\<YOUR_EC2_IP\>:30080 (\<AIRFLOW_USER\> / \<AIRFLOW_PASSWORD\>) |

> Real values (IP, credentials) are in `infra_local.md` — gitignored, not committed.

---

## Architecture

```
AWS EC2 t3.xlarge  (Elastic IP <YOUR_EC2_IP>)
└── K3S Kubernetes
    │
    ├── Pod 1: Apache Airflow
    │     DAG: Stock_Market_Pipeline
    │       extract() → transform() → load()   [Alpha Vantage daily OHLCV]
    │     DAG: API_Weather-Pull_Data
    │       extract() → transform() → load()   [Open-Meteo hourly temp]
    │           │ SQLAlchemy/pymysql
    │           ▼
    ├── MariaDB (172.31.23.236 / mariadb_private_ip / database_one)
    │     ├── stock_daily_prices
    │     └── weather_hourly
    │           │ SQLAlchemy query
    │           ▼
    ├── Pod 2: Flask + Dash  (NodePort 32147)
    │     /dashboard/ — candlestick + SMA, volume chart, stats table
    │     /health     — Kubernetes liveness probe
    │
    └── Persistent Volumes (hostPath on EC2)
          ├── DAG files  → /opt/airflow/dags
          ├── Airflow logs
          └── OutputTextWriter logs → /opt/airflow/out
```

---

## Tech Stack

| Category | Technology |
|---|---|
| Language | Python 3.12 |
| Data Orchestration | Apache Airflow 3.1.8 (Helm chart 1.20.0, TaskFlow API, LocalExecutor) |
| Web Framework | Flask 2.3.3 |
| Dashboarding | Dash 2.17.1 + Plotly 5.22.0 |
| Data Processing | Pandas 2.2.2 |
| Database | MariaDB (MySQL-compatible) |
| ORM / DB Driver | SQLAlchemy 2.0, pymysql 1.1.1 |
| App Server | Gunicorn 22.0.0 (2 workers) |
| Containerization | Docker (python:3.12-slim) + AWS ECR (private image registry) |
| Kubernetes | K3S (lightweight K8s, default containerd runtime) |
| Cloud | AWS EC2 t3.xlarge, 100 GiB EBS gp3 |
| Stock API | Alpha Vantage (TIME_SERIES_DAILY, 25 calls/day free) |
| Weather API | Open-Meteo (free, no key required) |
| Helm | Used for Airflow deployment on K3S |
| Planned | Snowflake (data warehouse), Apache Kafka (streaming) |

---

## File / Folder Structure

```
stock_live_data/
├── OVERVIEW.md                          # This file
├── README.md                            # High-level architecture sketch
├── notes.txt                            # Detailed setup/deployment notes (~69KB)
├── .gitignore
├── scripts/
│   └── deploy.sh                        # One-command deploy: syncs files, rebuilds Docker image, restarts pod
│
├── airflow/
│   ├── dags/                            # Airflow DAG files — mounted into pods via PVC
│   │   ├── dag_stocks.py                # Main DAG: Alpha Vantage daily OHLCV → MariaDB
│   │   ├── dag_weather.py               # Secondary DAG: Open-Meteo hourly temp → MariaDB
│   │   ├── stock_client.py              # Alpha Vantage API client (request + flatten)
│   │   ├── weather_client.py            # Open-Meteo API client (free, no key needed)
│   │   ├── file_logger.py               # OutputTextWriter: logs to PVC + stdout
│   │   ├── api_key.py                   # API keys (NOT committed — gitignored)
│   │   ├── db_config.py                 # DB credentials from env vars (NOT committed — gitignored)
│   │   └── constants.py                 # Local dev log path constant (NOT committed — gitignored)
│   ├── manifests/                       # Kubernetes resource definitions for Airflow
│   │   ├── pv-dags.yaml                 # PersistentVolume: mounts /home/ec2-user/airflow/dags into pods
│   │   ├── pvc-dags.yaml                # PersistentVolumeClaim for DAG files
│   │   ├── pv-airflow-logs.yaml         # PV for Airflow scheduler/webserver logs
│   │   ├── pvc-airflow-logs.yaml        # PVC for Airflow logs
│   │   ├── pv-output-logs.yaml          # PV for OutputTextWriter task logs
│   │   ├── pvc-output-logs.yaml         # PVC for OutputTextWriter logs
│   │   └── service-airflow-ui.yaml      # NodePort service: exposes Airflow UI on port 30080
│   ├── helm/
│   │   ├── values.yaml                  # Active Helm values for Airflow deployment
│   │   └── _archive/                    # Previous Helm value snapshots
│   └── _archive/                        # Old DAG files no longer in use
│
├── dashboard/
│   ├── app.py                           # Flask + Dash app: candlestick chart, volume, stats table
│   ├── Dockerfile                       # Builds my-flask-app:latest (python:3.12-slim, Gunicorn)
│   ├── requirements.txt                 # Python deps for the Docker image
│   └── manifests/
│       ├── pod-flask.yaml               # K8s Pod manifest (image: ECR URI, IfNotPresent pull, imagePullSecrets, namespace: default)
│       └── service-flask.yaml           # NodePort service: exposes dashboard on port 32147
│
├── logs/                                # OutputTextWriter log files (local dev only, gitignored)
└── airflow_env/                         # Python venv for local Airflow (not committed)
```

---

## Kubernetes Namespaces

A **namespace** is Kubernetes' way of logically partitioning a cluster into isolated groups. Resources in one namespace don't conflict with resources of the same name in another namespace, and you can apply different access controls, resource quotas, or Helm releases per namespace. Think of them like folders on a filesystem — the same filename can exist in two folders without collision.

### Why this project uses two namespaces

| Namespace | Contents | Why |
|---|---|---|
| `airflow-my-namespace` | All Airflow pods, PostgreSQL, PVCs, Secrets | Helm creates this namespace automatically and manages everything inside it. Keeping Airflow isolated prevents its many auto-generated resources from cluttering the default namespace. |
| `default` | Flask/Dash pod and its Service | Resources deployed manually with `kubectl apply` land in `default` by Kubernetes convention. Since Flask is a single pod — not a Helm chart — there's no reason to create a dedicated namespace for it. |

### Full resource inventory by namespace

**`airflow-my-namespace`** — managed by Helm

| Resource | Kind | Defined in |
|---|---|---|
| `airflow-scheduler-0` | Pod (StatefulSet) | Helm / `airflow/helm/values.yaml` |
| `airflow-api-server-*` | Pod (Deployment) | Helm / `airflow/helm/values.yaml` |
| `airflow-triggerer-0` | Pod (StatefulSet) | Helm / `airflow/helm/values.yaml` |
| `airflow-dag-processor-*` | Pod (Deployment) | Helm / `airflow/helm/values.yaml` |
| `airflow-postgresql-0` | Pod (StatefulSet) | Helm / `airflow/helm/values.yaml` |
| `airflow-service-expose-ui-port` | NodePort Service (port 30080) | `airflow/manifests/service-airflow-ui.yaml` |
| `pv-dags` / `pvc-dags` | PersistentVolume + Claim | `airflow/manifests/pv-dags.yaml`, `pvc-dags.yaml` |
| `pv-airflow-logs` / `pvc-airflow-logs` | PersistentVolume + Claim | `airflow/manifests/pv-airflow-logs.yaml`, `pvc-airflow-logs.yaml` |
| `pv-output-logs` / `pvc-output-logs` | PersistentVolume + Claim | `airflow/manifests/pv-output-logs.yaml`, `pvc-output-logs.yaml` |
| `db-credentials` | Secret | Created via `kubectl create secret` on EC2 (not a YAML file — contains DB password, API key) |

**`default`** — managed manually with `kubectl apply`

| Resource | Kind | Defined in |
|---|---|---|
| `my-kuber-pod-flask` | Pod | `dashboard/manifests/pod-flask.yaml` |
| `flask-service-expose-port` | NodePort Service (port 32147) | `dashboard/manifests/service-flask.yaml` |

### kubectl context default on EC2

The kubectl context on EC2 is set to `airflow-my-namespace`. Any `kubectl` command without a `-n` flag applies there. Always specify `-n default` for Flask resources:

```bash
kubectl get pods                      # shows airflow pods only
kubectl get pods -n default           # shows the flask pod
kubectl get pods --all-namespaces     # shows everything
```

---

## Dev Mode (Mac Laptop)

No Docker or Kubernetes needed. Run MariaDB natively, point the code at `localhost`, and run Flask and Airflow directly in a venv.

### 1. Install MariaDB
```bash
brew install mariadb
brew services start mariadb
# Connect and create the DB + user
sudo mysql -u root   # Mac: Homebrew MariaDB uses unix_socket auth for root
```
> **Note:** `mysql -u root` (without `sudo`) returns `ERROR 1698 (28000): Access denied` on Mac because Homebrew's MariaDB authenticates the root account via the OS user, not a password. The `sudo` prompt asks for your **Mac login password**. After running the SQL below to create `airflow_user`, all subsequent connections use that user with a password and do not need `sudo`.

> **Public repo / secrets:** Never commit real passwords or API keys. Store them in `db_config.py` and `api_key.py` — both are listed in `.gitignore` and never pushed. Use the placeholder `YOUR_DB_PASSWORD` below and substitute your real value only in your local `db_config.py`.

```sql
CREATE DATABASE database_one;
CREATE USER 'airflow_user'@'localhost' IDENTIFIED BY 'YOUR_DB_PASSWORD';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 2. Create secret files (never commit these)

Both files are in `.gitignore` and must be created locally — they are **never pushed to the public repo**.

**`airflow/dags/api_key.py`** — 3rd-party API keys:
```python
class api_keys:
    alpha_vantage = {"key": "YOUR_ALPHA_VANTAGE_KEY"}  # 25 free calls/day
    open_weather   = {"key": "YOUR_OPEN_WEATHER_KEY"}
```

**`airflow/dags/db_config.py`** — database credentials
*(create this file at `stock_live_data/airflow/dags/db_config.py`)*:
```python
DB_USER     = "airflow_user"       # created in step 1 SQL
DB_PASSWORD = "YOUR_DB_PASSWORD"   # password you chose in step 1
```

The DAG files and `flask_main.py` import `DB_USER` and `DB_PASSWORD` from `db_config` when building the SQLAlchemy connection string.

### 3. Create local `db_config.py` with database credentials

Create this file at `airflow/dags/db_config.py` (never commit to git — it's gitignored):
```python
DB_USER     = "airflow_user"
DB_PASSWORD = "YOUR_DB_PASSWORD"   # password you chose in step 1
DB_NAME     = "database_one"
DB_HOST     = "localhost"           # for dev mode on Mac (production uses 172.31.23.236 / mariadb_private_ip)
```

For the Flask dashboard, update `dashboard/app.py` line 42:
```python
SQL_URL = "localhost"   # for dev mode
# Production will use 172.31.23.236 (mariadb_private_ip) from the db-credentials Kubernetes Secret
```

### 4. Create local `api_key.py` with API keys

Create this file at `airflow/dags/api_key.py` (never commit to git — it's gitignored):
```python
class api_keys:
    alpha_vantage = {"key": "YOUR_ALPHA_VANTAGE_KEY"}  # 25 free calls/day
```

### 5. Update the logs path in `constants.py`
```python
# airflow/dags/constants.py
outputTextsFolder_folderPath = "/Users/<you>/path/to/stock_live_data/logs"
```
(Already set to David's Mac path — update if cloning on a different machine.)

### 6. Run the Flask dashboard
```bash
cd dashboard
pip install -r requirements.txt
python app.py
# Visit http://localhost:5000/dashboard/
```

### 7. Run Airflow DAGs
```bash
# From repo root
python -m venv airflow_env && source airflow_env/bin/activate
pip install apache-airflow pandas sqlalchemy pymysql requests

export AIRFLOW_HOME=$(pwd)
airflow db migrate        # replaces deprecated "airflow db init"
airflow users create --username admin --password admin \
  --firstname Air --lastname Flow --role Admin --email admin@example.com

airflow webserver &        # http://localhost:8080
airflow scheduler
# Enable the DAGs in the UI and trigger manually to test
```

---

## How to Deploy to Production (EC2 + K3S)

> Real values for `<YOUR_EC2_IP>`, `<YOUR_KEY_FILE>`, etc. are in `infra_local.md` (gitignored).
> Also note: the EC2 security group locks SSH to your current location's IP — update it in AWS Console if you can't connect.

### One-time infrastructure setup
1. Launch EC2 t3.xlarge, Amazon Linux 2023, 100 GiB gp3, assign Elastic IP.
2. Open inbound ports: 22, 30080 (Airflow UI), 32147 (Flask).
3. `curl -sfL https://get.k3s.io | sh -`  (installs K3S)
4. Install Helm, add Airflow repo: `helm repo add apache-airflow https://airflow.apache.org`

### Deploying Updates (DAGs and/or Dashboard)

All updates are deployed with a single command from the project root on your Mac:

```bash
./scripts/deploy.sh
```

**First time only** — make the script executable before running:
```bash
chmod +x scripts/deploy.sh
./scripts/deploy.sh
```

**What `scripts/deploy.sh` does (in order):**
1. Creates `/home/ec2-user/airflow/dags` and `/home/ec2-user/dashboard_build` on EC2 if they don't exist
2. `rsync airflow/dags/` → EC2: syncs all DAG files including gitignored secrets (`api_key.py`, `db_config.py`, `constants.py`) over encrypted SSH; only changed files are transferred
3. `rsync dashboard/` → EC2: syncs `app.py`, `Dockerfile`, `requirements.txt`, and manifests
4. Builds the Docker image on EC2, tags it, and pushes to AWS ECR: `docker build` → `docker push <ECR_REGISTRY>/my-flask-app:latest` (EC2 IAM role handles ECR authentication)
5. Refreshes the `ecr-credentials` Kubernetes secret with a fresh ECR token (valid 12h) so K3S containerd can pull the image
6. Deletes and recreates the Flask pod to pick up the new image from ECR
7. Prints pod status for verification

**SSH tunnel to view the UIs (recommended over opening Security Group ports):**
Rather than adding port rules to the AWS Security Group every time your IP changes, use an SSH tunnel — the ports stay closed in AWS and traffic travels through your existing encrypted SSH connection:
```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
# Then open: http://localhost:30080  (Airflow UI)
#            http://localhost:32147  (Dashboard)
```
> **Namespace note:** `pod-flask.yaml` and `service-flask.yaml` both declare `namespace: default`.
> The kubectl context on EC2 defaults to `airflow-my-namespace`, so running `kubectl apply -f` without
> an explicit namespace in the YAML would silently create the pod there instead — causing the service
> selector to match nothing and the NodePort to return no endpoints.

### Verify deployment
```bash
# Note: kubectl context is set to airflow-my-namespace; use -n default for flask resources
kubectl get pods --all-namespaces            # all Running
kubectl get svc -n default                   # NodePorts 30080 and 32147 listed
kubectl get endpoints -n default             # flask-service-expose-port should show an IP:5000 endpoint
kubectl logs -n default my-kuber-pod-flask
```

---

## Accessing the UIs (Dev and Production)

### Dev Mode (Mac laptop)

No tunnel needed — services run directly on localhost.

| Service | URL | How it's started |
|---|---|---|
| Airflow UI | http://localhost:8080 | `airflow webserver` in your venv |
| Flask/Dash Dashboard | http://localhost:5000/dashboard/ | `python dashboard/app.py` in your venv |

### Production — Private (current, SSH tunnel)

Ports 30080 and 32147 stay **closed** in the AWS Security Group. Traffic travels through your existing SSH connection — no IP rule changes needed, and the ports are never exposed to the public internet.

```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

Keep that terminal open (the tunnel lives as long as the SSH session). Then in your browser:

| Service | URL | Login |
|---|---|---|
| Airflow UI | http://localhost:30080 | admin / admin |
| Flask/Dash Dashboard | http://localhost:32147/dashboard/ | (none) |

**Tip — prevent idle disconnects:** Add `ServerAliveInterval 60` to the `ec2-stock` entry in `~/.ssh/config` so the SSH connection doesn't drop after a few minutes of inactivity:

```
Host ec2-stock
    ...
    ServerAliveInterval 60
```

### Production — Public (future, recruiter-facing)

When you want to share the dashboard with recruiters, open port 32147 publicly in AWS. Airflow (30080) stays closed — there's no reason to expose it.

**Steps in AWS Console:**
1. EC2 → Security Groups → select the group attached to your instance
2. Inbound Rules → Edit inbound rules → Add rule
3. Type: Custom TCP | Port: 32147 | Source: `0.0.0.0/0` | Description: "Flask dashboard public"
4. Save rules

The dashboard URL becomes: `http://<ELASTIC_IP>:32147/dashboard/`
(Real IP is in `infra_local.md` — gitignored)

> **Remove the rule when you're done.** Leaving port 32147 permanently open exposes the app to the public internet 24/7. Add it before a demo, remove it after.

**Future enhancement:** Point a custom domain's A record at the Elastic IP so the URL is human-readable (e.g., `http://stockdemo.yourdomain.com/dashboard/`). No Kubernetes changes needed — just a DNS A record.

---

## Production Status (as of 2026-03-30)

| Component | Status | Notes |
|---|---|---|
| Flask/Dash dashboard (`my-kuber-pod-flask`) | **Running** ✓ | Accessible via SSH tunnel on port 32147. Charts show empty until DAGs have run. |
| `airflow-api-server` | **Running** ✓ | Airflow 3.1.8 |
| `airflow-dag-processor` | **Running** ✓ | |
| `airflow-scheduler-0` | **Running** ✓ | |
| `airflow-triggerer-0` | **Running** ✓ | |
| `airflow-postgresql-0` | **Running** ✓ | Airflow internal metadata DB (separate from MariaDB) |
| MariaDB (`database_one`) | **Running** ✓ | `airflow_user` exists; app tables created automatically on first DAG run |
| DAGs (`dag_stocks`, `dag_weather`) | **Never triggered** | Airflow is running — needs one manual trigger (Step C below) |

### Production Issues Fixed (2026-03-30 and Earlier)

#### Recent Issues — Weather DAG Failures & Dashboard MariaDB Connection Error (2026-03-30)

**Symptom 1:** `API_Weather-Pull_Data` DAG showing 743 failed tasks and runs with no clear error messages.

**Symptom 2:** Flask dashboard displaying error: "Can't connect to MYSQL server on 'mariadb_private_ip' ([Errno -2] Name or service not known)"

**Root Causes:**

1. **Weather DAG code bugs:**
   - `weather_client.py` line 55: Typo `"celcius"` instead of `"celsius"` — invalid parameter sent to Open-Meteo API
   - `weather_client.py` line 69: Bare `except Exception: pass` silently swallowed all non-HTTPError exceptions (network failures, timeouts, etc.), causing cryptic downstream failures
   - `dag_weather.py` load() task: SQLAlchemy exceptions were caught but never re-raised — database connection failures appeared as task successes
   - Missing response validation: Code assumed Open-Meteo API response had required keys without checking, failing with cryptic KeyError if schema changed

2. **Database credentials not injected:**
   - Kubernetes Secret `db-credentials` didn't exist in the K3S cluster (or contained template placeholder `<mariadb_private_ip>` instead of actual value `172.31.23.236`)
   - Both Flask and Airflow pods reference this secret to populate environment variables (`DB_HOST`, `DB_USER`, `DB_PASSWORD`)
   - Without the secret, pods defaulted to missing credentials and couldn't connect to MariaDB

**Fixes Applied:**

1. **Code fixes** (committed to git):
   - Fixed `weather_client.py` line 55: `"celcius"` → `"celsius"`
   - Fixed `weather_client.py` line 69: Replace bare `except Exception: pass` with proper error logging and `raise`
   - Fixed `dag_weather.py` load() task: Added `raise` after SQLAlchemy exception handler
   - Added response validation in `dag_weather.py` extract() task: Check for required API response keys before processing

2. **Infrastructure fix** (one-time on EC2):
   - Created Kubernetes Secrets in both namespaces with actual database credentials:
     ```bash
     # In airflow-my-namespace
     kubectl create secret generic db-credentials \
       -n airflow-my-namespace \
       --from-literal=DB_USER=airflow_user \
       --from-literal=DB_PASSWORD=<new_password> \
       --from-literal=DB_NAME=database_one \
       --from-literal=DB_HOST=172.31.23.236 \
       --from-literal=ALPHA_VANTAGE_KEY=<api_key>

     # In default namespace (for Flask)
     kubectl create secret generic db-credentials \
       -n default \
       --from-literal=DB_USER=airflow_user \
       --from-literal=DB_PASSWORD=<new_password> \
       --from-literal=DB_NAME=database_one \
       --from-literal=DB_HOST=172.31.23.236 \
       --from-literal=ALPHA_VANTAGE_KEY=<api_key>
     ```
   - Restarted all pods to pick up the new secret

**Security Note:** Old exposed password `REDACTED_PASSWORD` was replaced with new password in MariaDB and all Kubernetes secrets. The old password is no longer valid.

**Related docs:** Full Airflow setup and issues in `docs/airflow-fix-2026-03-30.md`.

---

#### Earlier Issues — Airflow Infrastructure (2026-03-30)

Three cascading issues were resolved to get Airflow running initially. Full details in `docs/airflow-fix-2026-03-30.md`.

**Issue 1 — `values.yaml` was never reaching EC2**

`deploy.sh` only rsynced `airflow/dags/` and `dashboard/` — there was no step for `airflow/helm/`. Manual rsync attempts also ran from the wrong directory (relative paths only resolve from the project root). Result: every `helm upgrade` ran without the values file.

**Fix:** Ran rsync from the correct project root. Also permanently added **Step 2b** to `deploy.sh` so `airflow/helm/values.yaml` is synced on every deploy going forward.

---

**Issue 2 — Bitnami PostgreSQL image deleted from Docker Hub**

The Airflow Helm chart bundles its own PostgreSQL pod for internal metadata (DAG run history, task states, schedules). The pinned image `docker.io/bitnami/postgresql:16.1.0-debian-11-r15` had been deleted by Bitnami when they migrated to their own registry. The `airflow-postgresql-0` pod had been in `ImagePullBackOff` for 277 days, blocking all other Airflow pods via the `wait-for-airflow-migrations` init container.

**Fix:** Ran `helm upgrade` with chart `apache-airflow/airflow 1.20.0`. The updated chart references `bitnamilegacy/postgresql:16.1.0-debian-11-r15` (Docker Hub's legacy mirror), which is still available. Since `values.yaml` does not pin the PostgreSQL image, the upgrade automatically picked up the new default. Also deleted the stale 277-day-old scheduler and triggerer pods so the upgrade could recreate them cleanly.

---

**Issue 3 — DB credentials not available inside Airflow pods**

`db_config.py` reads `DB_PASSWORD`, `DB_USER`, `DB_HOST`, etc. from environment variables (defaulting to `""` if absent). Without those env vars set inside the pods, every `load()` task would fail with `Access Denied`.

**Fix (two parts):**

1. Created a Kubernetes Secret on EC2 with the actual MariaDB private IP (172.31.23.236 / mariadb_private_ip):
```bash
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=YOUR_DB_PASSWORD \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=172.31.23.236 \
  --from-literal=ALPHA_VANTAGE_KEY=YOUR_ALPHA_VANTAGE_KEY \
  --dry-run=client -o yaml | kubectl apply -f -

# Also create the same secret in the default namespace for Flask pod:
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=YOUR_DB_PASSWORD \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=172.31.23.236 \
  --from-literal=ALPHA_VANTAGE_KEY=YOUR_ALPHA_VANTAGE_KEY \
  --dry-run=client -o yaml | kubectl apply -f -
```

2. Added a top-level `extraEnvFrom` block to `airflow/helm/values.yaml` to inject the secret into all Airflow pods:
```yaml
extraEnvFrom: |
  - secretRef:
      name: db-credentials
```
Note: `extraEnvFrom` must be at the **top level** of `values.yaml`. Placing it under `scheduler:` or `triggerer:` causes a schema validation error (`Additional property extraEnvFrom is not allowed`).

---

### Next step: trigger the DAGs (Step C)

Airflow is running and credentials are injected. The application tables (`stock_daily_prices`, `weather_hourly`) don't exist yet — both DAGs run `CREATE TABLE IF NOT EXISTS` in their `load()` task, so tables are created automatically on the first successful run.

```bash
# Open SSH tunnel on your Mac:
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```
1. Open `http://localhost:30080` — login `admin` / `admin`
2. Trigger `dag_stocks` and `dag_weather` manually once each
3. Confirm both tasks complete green (especially `load()` — that's where the DB connection happens)
4. Open `http://localhost:32147/dashboard/` — candlestick chart should now show real OHLCV data

### MariaDB

`database_one` and `airflow_user` exist and are healthy. `airflow_user` is granted access from both the K8s pod subnet (`10.42.%`) and the EC2 private IP. No action needed here.

### Dashboard

The Flask/Dash pod is healthy and serving. Since `stock_daily_prices` doesn't exist yet, it currently shows empty charts. It will show real data as soon as Step C above is completed.

### `deploy.sh`

The deploy script is complete and handles all syncing automatically:
- Syncs DAG files + gitignored secrets to EC2
- **Syncs `airflow/helm/values.yaml` to EC2** (Step 2b — added 2026-03-30)
- Rebuilds the Flask Docker image and pushes to ECR
- Refreshes the `ecr-credentials` Kubernetes secret
- Restarts the Flask pod

The Airflow pods do not need to be restarted when only DAG files change — the PVC mount makes new files visible to the scheduler immediately.

---

## Planned Enhancements (Step 2)

| Feature | Description |
|---|---|
| Snowflake | Replace MariaDB with Snowflake; use `SnowflakeHook` + `write_pandas()` in DAGs. Packages already commented in `requirements.txt`. |
| Apache Kafka | Airflow publishes to Kafka topics; a separate consumer pod loads into the database. Skeleton producer code exists in `api_weather_requests.py`. |
| More tickers | Alpha Vantage free tier: 25 calls/day. With 1 retry per ticker the safe ceiling is **12 tickers** (12 × 2 = 24 calls). An `assert len(TICKERS) <= 12` guard in `dag_stocks.py extract()` enforces this — adding a 13th ticker fails loudly at task start instead of silently mid-run. Upgrade to a paid Alpha Vantage tier to raise the limit. |
| Weather dashboard | `weather_hourly` table is populated but not yet visualized in the Dash app. |
