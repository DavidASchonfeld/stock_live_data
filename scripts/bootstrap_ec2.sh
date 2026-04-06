#!/bin/bash
# Provision a fresh Ubuntu 24.04 EC2 instance: install stack, restore MariaDB, configure K3s/Airflow.
# Automates Runbook #15 Phases C-E. Run from Mac; drives new EC2 via SSH/SCP.
# Usage:   ./scripts/bootstrap_ec2.sh <temp-ssh-host>
# Example: ./scripts/bootstrap_ec2.sh ec2-ubuntu-temp
#
# Prerequisites (manual steps before running):
#   1. Launch Ubuntu 24.04 EC2 in AWS Console (same t3.large, security group, IAM role, key pair)
#   2. Add temp SSH config entry pointing to new instance's public IP (see RUNBOOKS.md §15 Phase B)
#   3. Check Airflow chart version: ssh ec2-stock helm list -n airflow-my-namespace
#   4. Confirm /tmp/db_backup.sql exists on your Mac (mysqldump from old instance)

set -euo pipefail

# ── User-configurable: set AIRFLOW_CHART_VERSION before running ───────────────
# Find the current version by running: ssh ec2-stock helm list -n airflow-my-namespace
# Look at the CHART column (e.g. "airflow-1.15.0" → set "1.15.0" here)
AIRFLOW_CHART_VERSION="1.15.0"
# ─────────────────────────────────────────────────────────────────────────────

# ── Resolve paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
KEY_PATH="$HOME/Documents/Programming/Python/Data-Pipeline-2026/kafkaProjectKeyPair_4-29-2025.pem"
ENV_DEPLOY="$PROJECT_ROOT/.env.deploy"
DB_BACKUP_LOCAL="/tmp/db_backup.sql"
# ─────────────────────────────────────────────────────────────────────────────

# ── Load .env.deploy (ECR_REGISTRY, AWS_REGION) ───────────────────────────────
if [ ! -f "$ENV_DEPLOY" ]; then
    echo "ERROR: $ENV_DEPLOY not found."
    echo "Copy .env.deploy.example to .env.deploy and fill in your AWS values."
    exit 1
fi
# shellcheck source=../.env.deploy
source "$ENV_DEPLOY"

for var in ECR_REGISTRY AWS_REGION; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set in .env.deploy"; exit 1
    fi
done
# ─────────────────────────────────────────────────────────────────────────────

# ── Validate argument ─────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    echo "Usage: $0 <temp-ssh-host>"
    echo "  temp-ssh-host: SSH config alias for the new Ubuntu instance (e.g. ec2-ubuntu-temp)"
    exit 1
fi
NEW_HOST="$1"
# ─────────────────────────────────────────────────────────────────────────────

# ── Preflight checks ──────────────────────────────────────────────────────────
if [ ! -f "$DB_BACKUP_LOCAL" ]; then
    echo "ERROR: $DB_BACKUP_LOCAL not found on your Mac."
    echo "Run on old instance:  sudo mysqldump -u root database_one > /tmp/db_backup.sql"
    echo "Then copy to Mac:     scp ec2-stock:/tmp/db_backup.sql /tmp/db_backup.sql"
    exit 1
fi

if [ ! -f "$KEY_PATH" ]; then
    echo "ERROR: SSH key not found at $KEY_PATH"; exit 1
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Collect secrets upfront (so the script never blocks mid-flight) ───────────
echo ""
echo "=== Collecting secrets (input is hidden) ==="

while true; do
    read -rsp "DB_PASSWORD for airflow_user: " DB_PASSWORD; echo
    read -rsp "Confirm DB_PASSWORD: " DB_PASSWORD_CONFIRM; echo
    if [ "$DB_PASSWORD" = "$DB_PASSWORD_CONFIRM" ]; then break; fi
    echo "Passwords do not match. Try again."
done

read -rsp "AIRFLOW_ADMIN_PASSWORD (save this — you'll use it to log in): " AIRFLOW_ADMIN_PASSWORD; echo
# NOTE: AIRFLOW_ADMIN_PASSWORD is collected here for reference but is NOT currently passed to
# helm install (Helm chart default credentials are admin/admin). Change this after EIP cutover
# via Airflow UI: Security → List Users → edit admin user. See Runbook #15 Known Issues.
read -rp  "SEC_EDGAR_EMAIL (e.g. your-name@example.com Your Name): " SEC_EDGAR_EMAIL
read -rsp "SLACK_WEBHOOK_URL (press Enter to leave blank — log-only mode): " SLACK_WEBHOOK_URL; echo
echo ""
# ─────────────────────────────────────────────────────────────────────────────

# ── SSH/SCP helper functions ──────────────────────────────────────────────────
# accept-new: auto-accept the host key on first connect; reject changed keys after
ec2_ssh() { ssh -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "$NEW_HOST" "$@"; }
ec2_scp() { scp -i "$KEY_PATH" -o StrictHostKeyChecking=accept-new "$1" "$NEW_HOST:$2"; }
# ─────────────────────────────────────────────────────────────────────────────

echo "=== Starting bootstrap for host: $NEW_HOST ==="
echo ""

# ═════════════════════════════════════════════════════════════════════════════
# Phase C: Install packages and tools
# ═════════════════════════════════════════════════════════════════════════════
echo "=== Phase C: Updating apt and installing base packages ==="
ec2_ssh "sudo apt-get update -y && sudo apt-get install -y mariadb-server docker.io unzip curl"
ec2_ssh "sudo systemctl enable --now mariadb docker"
# Add ubuntu to docker group so deploy.sh can run docker commands without sudo
ec2_ssh "sudo usermod -aG docker ubuntu"

echo "=== Phase C: Installing AWS CLI v2 ==="
# apt install awscli on Ubuntu 24.04 gives CLI v1 (deprecated); use the official v2 installer
ec2_ssh "curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip \
    && unzip -q /tmp/awscliv2.zip -d /tmp/awscliv2-install \
    && sudo /tmp/awscliv2-install/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/awscliv2-install"

echo "=== Phase C: Installing K3s ==="
ec2_ssh "curl -sfL https://get.k3s.io | sh -"

echo "=== Phase C: Configuring kubectl for ubuntu user ==="
ec2_ssh "sudo chmod 644 /etc/rancher/k3s/k3s.yaml \
    && mkdir -p ~/.kube \
    && cp /etc/rancher/k3s/k3s.yaml ~/.kube/config \
    && grep -qxF 'export KUBECONFIG=~/.kube/config' ~/.bashrc \
        || echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc"

echo "=== Phase C: Installing Helm ==="
ec2_ssh "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
ec2_ssh "helm repo add apache-airflow https://airflow.apache.org && helm repo update"

echo "=== Phase C: Waiting for K3s node to be Ready (up to 5 minutes) ==="
ec2_ssh "
    for i in \$(seq 1 30); do
        if kubectl get nodes 2>/dev/null | grep -q ' Ready'; then
            echo 'K3s node is Ready'; kubectl get nodes; break
        fi
        echo \"Attempt \$i/30 — K3s not ready yet, waiting 10s...\"; sleep 10
        if [ \$i -eq 30 ]; then echo 'ERROR: K3s did not become Ready after 5 minutes'; exit 1; fi
    done
"

# ═════════════════════════════════════════════════════════════════════════════
# Phase D: Restore MariaDB
# ═════════════════════════════════════════════════════════════════════════════
echo "=== Phase D: Uploading DB backup to EC2 ==="
ec2_scp "$DB_BACKUP_LOCAL" "/tmp/db_backup.sql"

echo "=== Phase D: Creating database and importing backup ==="
ec2_ssh "sudo mysql -e 'CREATE DATABASE IF NOT EXISTS database_one;'"
ec2_ssh "sudo mysql database_one < /tmp/db_backup.sql"

echo "=== Phase D: Recreating airflow_user with grants ==="
# Grants cover 10.42.% (K3s pod network) and the instance's own private IP
ec2_ssh "
    NEW_IP=\$(hostname -I | awk '{print \$1}')
    echo \"Detected private IP: \$NEW_IP\"
    sudo mysql <<SQL
CREATE USER IF NOT EXISTS 'airflow_user'@'10.42.%' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS 'airflow_user'@'\${NEW_IP}' IDENTIFIED BY '${DB_PASSWORD}';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'10.42.%';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'\${NEW_IP}';
FLUSH PRIVILEGES;
SQL
"

echo "=== Phase D: Setting MariaDB bind-address to 0.0.0.0 ==="
ec2_ssh "sudo sed -i 's/^bind-address\s*=.*/bind-address = 0.0.0.0/' \
    /etc/mysql/mariadb.conf.d/50-server.cnf \
    && sudo systemctl restart mariadb"

# ═════════════════════════════════════════════════════════════════════════════
# Phase E: Configure K3s and install Airflow
# ═════════════════════════════════════════════════════════════════════════════
echo "=== Phase E: Creating airflow-my-namespace ==="
# dry-run|apply is idempotent (no AlreadyExists error on re-run)
ec2_ssh "kubectl create namespace airflow-my-namespace --dry-run=client -o yaml | kubectl apply -f -"

echo "=== Phase E: Creating host directories ==="
ec2_ssh "mkdir -p /home/ubuntu/airflow/dags \
    /home/ubuntu/airflow/dag-mylogs \
    /home/ubuntu/airflow/helm \
    /home/ubuntu/airflow/manifests \
    && chmod 777 /home/ubuntu/airflow/dag-mylogs \
    && sudo mkdir -p /opt/airflow/logs /opt/airflow/out \
    && sudo chown -R ubuntu:ubuntu /opt/airflow"
# chmod 777 on dag-mylogs: Airflow pod runs as UID 50000, not ubuntu (1000) — needs world-write to reach PVC-backed log dir

echo "=== Phase E: Syncing manifests to EC2 ==="
for f in pv-dags.yaml pv-airflow-logs.yaml pv-output-logs.yaml \
          pvc-dags.yaml pvc-airflow-logs.yaml pvc-output-logs.yaml \
          service-airflow-ui.yaml; do
    ec2_scp "$PROJECT_ROOT/airflow/manifests/$f" "/home/ubuntu/airflow/manifests/$f"
done
ec2_scp "$PROJECT_ROOT/airflow/helm/values.yaml" "/home/ubuntu/airflow/helm/values.yaml"

echo "=== Phase E: Applying PV manifests ==="
for f in pv-dags.yaml pv-airflow-logs.yaml pv-output-logs.yaml; do
    ec2_ssh "kubectl apply -f /home/ubuntu/airflow/manifests/$f"
done

echo "=== Phase E: Applying PVC manifests ==="
for f in pvc-dags.yaml pvc-airflow-logs.yaml pvc-output-logs.yaml; do
    # -n flag ensures correct namespace even for manifests without namespace in metadata
    ec2_ssh "kubectl apply -f /home/ubuntu/airflow/manifests/$f -n airflow-my-namespace"
done

echo "=== Phase E: Applying Airflow service manifest ==="
ec2_ssh "kubectl apply -f /home/ubuntu/airflow/manifests/service-airflow-ui.yaml \
    -n airflow-my-namespace"

echo "=== Phase E: Creating db-credentials secret in both namespaces ==="
ec2_ssh "
    NEW_IP=\$(hostname -I | awk '{print \$1}')
    for NS in airflow-my-namespace default; do
        kubectl create secret generic db-credentials -n \$NS \
            --from-literal=DB_USER=airflow_user \
            --from-literal=DB_PASSWORD='${DB_PASSWORD}' \
            --from-literal=DB_HOST=\$NEW_IP \
            --from-literal=DB_NAME=database_one \
            --from-literal=SEC_EDGAR_EMAIL='${SEC_EDGAR_EMAIL}' \
            --from-literal=SLACK_WEBHOOK_URL='${SLACK_WEBHOOK_URL}' \
            --dry-run=client -o yaml | kubectl apply -n \$NS -f -
        echo \"db-credentials created in namespace: \$NS\"
    done
"

echo "=== Phase E: Installing Airflow via Helm (chart v${AIRFLOW_CHART_VERSION}) ==="
# NOTE: values.yaml already includes 3 known fixes required on t3.large with Helm chart 1.15.0:
#   1. postgresql.image → ECR Public (bitnami tag 16.1.0-debian-11-r15 was removed from Docker Hub;
#      fallback to bitnami/postgresql:16-debian-12 also failed — also removed)
#   2. webserver.startupProbe failureThreshold: 18 → 180s startup window
#      (default 60s is too short on t3.large: gunicorn + provider loading takes 60–100s;
#       pod was killed with SIGTERM/exit 0, making it look like a clean exit rather than a probe kill)
#   3. triggerer.resources.limits.memory: 512Mi (was 256Mi — OOMKilled at startup during provider load)
# See Runbook #15 "Known Issues" and PLAIN_ENGLISH_GUIDE.md Bugs 7–9 for full details.
ec2_ssh "helm install airflow apache-airflow/airflow \
    -n airflow-my-namespace \
    --version ${AIRFLOW_CHART_VERSION} \
    -f /home/ubuntu/airflow/helm/values.yaml \
    || echo 'NOTE: helm install failed (already installed?). Run: helm upgrade airflow apache-airflow/airflow -n airflow-my-namespace --version ${AIRFLOW_CHART_VERSION} -f ~/airflow/helm/values.yaml'"

# ═════════════════════════════════════════════════════════════════════════════
# Done
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Your Airflow admin password: ${AIRFLOW_ADMIN_PASSWORD}"
echo "(Save this — you'll use it to log in to the Airflow UI)"
echo ""
echo "Next steps (Step 7 of Runbook #15):"
echo ""
echo "  1. Wait 3-5 min for Airflow pods to start:"
echo "       ssh $NEW_HOST kubectl get pods -n airflow-my-namespace -w"
echo ""
echo "  2. Run the first deploy (syncs DAGs, builds + pushes Flask image):"
echo "       Edit scripts/deploy.sh line ~34: change EC2_HOST to \"$NEW_HOST\""
echo "       Then: ./scripts/deploy.sh"
echo "       Then revert EC2_HOST back to \"ec2-stock\" (don't commit the temp value)"
echo ""
echo "  3. Open SSH tunnels to verify:"
echo "       ssh -L 30080:localhost:30080 -L 32147:localhost:32147 $NEW_HOST"
echo "       Airflow UI:  http://localhost:30080"
echo "       Dashboard:   http://localhost:32147/dashboard/"
echo ""
echo "  4. Verify checklist (Step 8):"
echo "       - All pods Running:     kubectl get pods -A"
echo "       - Airflow UI loads and both DAGs run successfully"
echo "       - Dashboard shows data"
echo "       - Memory OK:            free -h   (used < 6 GB)"
echo ""
echo "  5. Move Elastic IP in AWS Console (52.70.211.1) from old instance to this one"
echo "     Then update ~/.ssh/config: ec2-stock HostName → new instance's private IP"
echo "     Then revert deploy.sh EC2_HOST → ec2-stock"
echo ""
echo "  See RUNBOOKS.md §15 Phases F-I for EIP cutover and cleanup steps."
