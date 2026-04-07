# Setup and Operations Guide

This document covers local development setup, production deployment details, and Kubernetes namespace layout. For the project summary, architecture, and tech stack, see [README.md](README.md).

---

## Local Development (Mac)

No Docker or Kubernetes needed. Run MariaDB natively, point the code at `localhost`, and run Flask and Airflow directly in a venv.

### 1. Install MariaDB
```bash
brew install mariadb
brew services start mariadb
sudo mysql -u root   # Homebrew uses unix_socket auth — needs sudo
```
> `mysql -u root` (without `sudo`) returns `Access denied` on Mac because Homebrew authenticates root via the OS user. After creating `airflow_user` below, all connections use that user with a password.

```sql
CREATE DATABASE database_one;
CREATE USER 'airflow_user'@'localhost' IDENTIFIED BY 'YOUR_DB_PASSWORD';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

### 2. Create secret files (never commit these)

**`.env`** — all local secrets *(at `data_pipeline/.env`)*:
```bash
DB_USER=airflow_user
DB_PASSWORD=YOUR_DB_PASSWORD
DB_NAME=database_one
DB_HOST=localhost
EDGAR_CONTACT_EMAIL=your.email@gmail.com
```

**`airflow/dags/db_config.py`**:
```python
DB_USER     = "airflow_user"
DB_PASSWORD = "YOUR_DB_PASSWORD"
DB_NAME     = "database_one"
DB_HOST     = "localhost"
```

### 3. Update the logs path
```python
# airflow/dags/constants.py
outputTextsFolder_folderPath = "/Users/<you>/path/to/data_pipeline/logs"
```

### 4. Run the Flask dashboard
```bash
cd dashboard
pip install -r requirements.txt
python app.py
# Visit http://localhost:5000/dashboard/
```

### 5. Run Airflow
```bash
python -m venv airflow_env && source airflow_env/bin/activate
pip install apache-airflow pandas sqlalchemy pymysql requests
export AIRFLOW_HOME=$(pwd)
airflow db migrate
airflow users create --username admin --password admin \
  --firstname Air --lastname Flow --role Admin --email admin@example.com
airflow webserver &        # http://localhost:8080
airflow scheduler
```

---

## Production Deployment (EC2 + K3S)

> Real values for IPs and credentials are in `infra_local.md` (gitignored).

### One-time infrastructure setup
1. Launch EC2 t3.large, Ubuntu 24.04 LTS, 100 GiB gp3, assign Elastic IP
2. Open inbound ports: 22, 30080 (Airflow UI), 32147 (Flask)
3. Install K3S: `curl -sfL https://get.k3s.io | sh -`
4. Install Helm, add Airflow repo: `helm repo add apache-airflow https://airflow.apache.org`

### First-time: create `.env.deploy`
```bash
cp .env.deploy.example .env.deploy
# Fill in: ECR_REGISTRY="<AWS_ACCOUNT_ID>.dkr.ecr.<REGION>.amazonaws.com", AWS_REGION
```

### Deploy updates
```bash
./scripts/deploy.sh
```

What deploy.sh does: validates DAG syntax → rsyncs files to EC2 → builds Docker image → pushes to ECR → applies Helm values → restarts pods → verifies.

### Accessing the UIs

**SSH tunnel (recommended)** — keeps ports closed in AWS:
```bash
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
# Airflow UI:  http://localhost:30080
# Dashboard:   http://localhost:32147/dashboard/
```

**Public access (for demos):** Open port 32147 in the EC2 Security Group inbound rules. Remove the rule after the demo.

---

## Kubernetes Namespaces

Pods are organized into namespaces — logical partitions within the cluster.

| Namespace | Contents | Why separate |
|---|---|---|
| `airflow-my-namespace` | All Airflow pods, PostgreSQL, PVCs, Secrets | Helm manages everything here; keeps auto-generated resources isolated |
| `default` | Flask/Dash pod and its Service | Single pod deployed manually — no need for a dedicated namespace |

### Key resources by namespace

**`airflow-my-namespace`** (Helm-managed):
- Pods: `airflow-scheduler-0`, `airflow-api-server-*`, `airflow-triggerer-0`, `airflow-dag-processor-*`, `airflow-postgresql-0`
- Services: `airflow-service-expose-ui-port` (NodePort 30080)
- Storage: PV/PVC pairs for DAG files, Airflow logs, and task output logs
- Secrets: `db-credentials` (DB_USER, DB_PASSWORD, DB_HOST, DB_NAME, EDGAR_CONTACT_EMAIL)

**`default`** (manually applied):
- Pod: `my-kuber-pod-flask`
- Service: `flask-service-expose-port` (NodePort 32147)

### kubectl context
The kubectl context on EC2 defaults to `airflow-my-namespace`. Always specify `-n default` for Flask resources:
```bash
kubectl get pods                      # shows Airflow pods only
kubectl get pods -n default           # shows the Flask pod
kubectl get pods --all-namespaces     # shows everything
```

---

## Project Structure

```
data_pipeline/
├── airflow/
│   ├── dags/                   DAG files + support modules (mounted into pods via PVC)
│   ├── helm/values.yaml        Active Helm values for Airflow deployment
│   └── manifests/              K8s PV/PVC/Service YAML files
├── dashboard/
│   ├── app.py                  Flask + Dash entry point
│   ├── db.py, charts.py, routes.py, callbacks.py
│   ├── Dockerfile              Builds my-flask-app:latest
│   └── manifests/              Pod and Service YAML
├── docs/                       Full documentation (see docs/INDEX.md)
├── scripts/deploy.sh           One-command deploy script
├── .env.deploy.example         Template for AWS deploy secrets
└── README.md                   Project summary and architecture
```

For detailed file descriptions, see [README.md](README.md).
