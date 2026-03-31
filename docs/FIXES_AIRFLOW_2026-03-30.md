# Airflow Production Fix — 2026-03-30

**Quick Navigation**
- Want to understand Helm or Airflow? See [ARCHITECTURE.md](ARCHITECTURE.md)
- Need debugging guidance? See [DEBUGGING.md](DEBUGGING.md)
- Want term definitions (ImagePullBackOff, Helm, etc.)? See [GLOSSARY.md](GLOSSARY.md)
- Looking for command reference? See [COMMANDS.md](COMMANDS.md)

---

## What was broken and why

Airflow had **never successfully started** on EC2. The root cause was a cascade of three issues:

### Issue 1 — `values.yaml` never reached EC2

`deploy.sh` only rsyncs `airflow/dags/` and `dashboard/` — it has no step for `airflow/helm/`. So `helm upgrade` had been attempted without a values file, and manual attempts to rsync it manually were run from the wrong directory. `deploy.sh` uses relative paths from the project root (`stock_live_data/`), so `airflow/helm/values.yaml` only resolves correctly when rsync is run from that folder.

**Fix:** Ran rsync from the correct project root:
```bash
cd /Users/David/Documents/Programming/Python/StockLiveData/stock_live_data
rsync -avz airflow/helm/values.yaml ec2-stock:/home/ec2-user/airflow/helm/values.yaml
```
Then updated `deploy.sh` permanently to also sync this file on every deploy (new Step 2b).

---

### Issue 2 — Bitnami PostgreSQL image deleted from Docker Hub

The Airflow Helm chart bundles its own PostgreSQL pod for Airflow's internal metadata database (DAG run history, task states, schedules). This is separate from the project's MariaDB instance.

The bundled image `docker.io/bitnami/postgresql:16.1.0-debian-11-r15` had been deleted from Docker Hub by Bitnami (they purged old tags when migrating to their own registry). The `airflow-postgresql-0` pod had been in `ImagePullBackOff` for **277 days**.

Because PostgreSQL never started, an init container called `wait-for-airflow-migrations` blocked every other Airflow pod (scheduler, triggerer, API server) indefinitely.

**Fix:** Ran `helm upgrade` with the current chart version (`apache-airflow/airflow 1.20.0`). The chart now uses `bitnamilegacy/postgresql:16.1.0-debian-11-r15` — Docker Hub's legacy mirror path — which is still available. Since `values.yaml` does not pin the PostgreSQL image, the upgrade automatically picked up the chart's new default. PostgreSQL came up healthy immediately.

We also deleted the stale `airflow-scheduler-0` and `airflow-triggerer-0` pods that were left over from a previous Airflow 2.x installation (277 days old, wrong image tag, missing secrets) so the upgrade could recreate them cleanly.

---

### Issue 3 — DB credentials not available to Airflow pods

`db_config.py` reads `DB_PASSWORD`, `DB_USER`, `DB_HOST`, etc. from environment variables (defaulting to `""` if absent). Without those env vars set inside the Kubernetes pods, every DAG `load()` task would fail with `Access Denied` when connecting to MariaDB.

**Fix (two parts):**

**Part 1 — Create Kubernetes Secret on EC2:**
```bash
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=<password> \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=172.31.23.236 \
  --from-literal=ALPHA_VANTAGE_KEY=<key> \
  --dry-run=client -o yaml | kubectl apply -f -
```

**Part 2 — Mount the secret into all pods via `values.yaml`:**

The Airflow Helm chart supports a top-level `extraEnvFrom` field that injects env vars from a Kubernetes Secret into every Airflow pod (scheduler, triggerer, API server, dag-processor). Added to `values.yaml`:

```yaml
extraEnvFrom: |
  - secretRef:
      name: db-credentials
```

Note: `extraEnvFrom` is only valid at the **top level** of `values.yaml`. Placing it under `scheduler:` or `triggerer:` sections causes a schema validation error (`Additional property extraEnvFrom is not allowed`).

After syncing `values.yaml` to EC2 and running `helm upgrade` again, all pods came up healthy.

---

## Changes made to project files

### `scripts/deploy.sh`
- Added `EC2_HELM_PATH="/home/ec2-user/airflow/helm"` variable
- Extended the `mkdir -p` in Step 1 to include `$EC2_HELM_PATH`
- Added **Step 2b** that rsyncs `airflow/helm/values.yaml` to EC2 on every deploy

### `airflow/helm/values.yaml`
- Added top-level `extraEnvFrom` block referencing the `db-credentials` Kubernetes Secret
- This injects `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_HOST`, and `ALPHA_VANTAGE_KEY` into all Airflow pods at runtime

---

## Current state of everything (as of 2026-03-30)

### Kubernetes pods — `airflow-my-namespace`

| Pod | Ready | Status |
|-----|-------|--------|
| `airflow-api-server` | 1/1 | Running |
| `airflow-dag-processor` | 2/2 | Running |
| `airflow-postgresql-0` | 1/1 | Running |
| `airflow-scheduler-0` | 2/2 | Running |
| `airflow-statsd` | 1/1 | Running |
| `airflow-triggerer-0` | 2/2 | Running |

### Kubernetes pods — `default` namespace

| Pod | Ready | Status |
|-----|-------|--------|
| `my-kuber-pod-flask` | 1/1 | Running |

### Helm release

| Detail | Value |
|--------|-------|
| Chart | `apache-airflow/airflow 1.20.0` |
| Airflow version | `3.1.8` |
| Revision | `4` |
| Status | `deployed` |

### What still needs to be done (Step C)

Airflow is running and credentials are injected, but **the DAGs have never been triggered**. The application database tables (`stock_daily_prices`, `weather_hourly`) don't exist yet — they are created automatically by `CREATE TABLE IF NOT EXISTS` on the first successful DAG run.

1. Open the SSH tunnel on your Mac:
   ```bash
   ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
   ```
2. Open `http://localhost:30080` — login `admin` / `admin`
3. Trigger `dag_stocks` and `dag_weather` manually once each
4. Confirm both tasks complete green (especially the `load()` task — that's where DB connection happens)
5. Open the dashboard at `http://localhost:32147/dashboard/` — the candlestick chart should now show real OHLCV data for AAPL, MSFT, GOOGL

### Dashboard
The Flask/Dash pod (`my-kuber-pod-flask`) is healthy and serving. However, because `stock_daily_prices` doesn't exist yet, it currently shows an error or empty charts. It will show real data as soon as step C above is completed.

### MariaDB
`database_one` and `airflow_user` exist and are healthy. The `airflow_user` is granted access from the K8s pod subnet (`10.42.%`) and the EC2 private IP (`172.31.23.236`). No action needed here.

### `deploy.sh`
The deploy script is now complete and handles all syncing automatically. Running `./scripts/deploy.sh` from the project root will sync DAGs, Helm values, rebuild the Flask Docker image, push to ECR, and restart the Flask pod. The Airflow pods do not need to be restarted when only DAG files change — the PVC mount makes new files visible to the scheduler immediately.
