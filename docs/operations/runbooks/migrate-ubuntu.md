# Runbook 15: Migrate EC2 from AL2023 to Ubuntu 24.04 LTS

> Part of the [Runbooks Index](../RUNBOOKS.md).

**When:** Moving from Amazon Linux 2023 to Ubuntu 24.04 LTS for post-quantum SSH support (OpenSSH 9.6+) and long-term maintainability.

**Why not AMI copy?** This is an OS change — everything must be installed fresh. Data is exported and imported.

**Key differences:**

| Thing | AL2023 | Ubuntu 24.04 |
|---|---|---|
| Package manager | `dnf` | `apt` |
| SSH user | `ec2-user` | `ubuntu` |
| MariaDB package | `mariadb105-server` | `mariadb-server` |
| OpenSSH | 8.7p1 | 9.6p1 (post-quantum) |

---

### Phase A — Backup (old instance)

```bash
ssh ec2-stock
mysqldump -u root database_one > /tmp/db_backup.sql
mysql -u root -e "SHOW GRANTS FOR 'airflow_user'@'10.42.%';"
exit
scp ec2-stock:/tmp/db_backup.sql /tmp/db_backup.sql
```

### Phase B — Launch Ubuntu instance

1. AMI: Ubuntu Server 24.04 LTS by Canonical (amd64)
2. Instance type: t3.large, key pair, security group, **IAM role** — all same as old instance
3. Do NOT move Elastic IP yet — keep old instance as fallback

### Phase C — Install stack

> Automated: `./scripts/bootstrap_ec2.sh ec2-ubuntu-temp` handles Phases C–E.

Manual steps for reference:

```bash
ssh -i .../your-key.pem ubuntu@<TEMP_IP>

# C1. System update
sudo apt update && sudo apt upgrade -y

# C2. MariaDB
sudo apt install -y mariadb-server
sudo systemctl enable --now mariadb

# C3. Docker
sudo apt install -y docker.io
sudo usermod -aG docker ubuntu && newgrp docker

# C4. AWS CLI
sudo apt install -y awscli
aws sts get-caller-identity   # verify IAM role works

# C5. K3s
curl -sfL https://get.k3s.io | sh -
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown ubuntu:ubuntu ~/.kube/config
echo 'export KUBECONFIG=~/.kube/config' >> ~/.bashrc
export KUBECONFIG=~/.kube/config

# C6. Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
```

### Phase D — Restore data

```bash
scp /tmp/db_backup.sql ubuntu@<TEMP_IP>:/tmp/   # from Mac
ssh ubuntu@<TEMP_IP>

sudo mysql -e "CREATE DATABASE IF NOT EXISTS database_one;"
sudo mysql database_one < /tmp/db_backup.sql

NEW_IP=$(hostname -I | awk '{print $1}')
sudo mysql <<EOF
CREATE USER IF NOT EXISTS 'airflow_user'@'10.42.%' IDENTIFIED BY '<password>';
CREATE USER IF NOT EXISTS 'airflow_user'@'$NEW_IP' IDENTIFIED BY '<password>';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'10.42.%';
GRANT ALL PRIVILEGES ON database_one.* TO 'airflow_user'@'$NEW_IP';
FLUSH PRIVILEGES;
EOF

sudo sed -i 's/^bind-address\s*=.*/bind-address = 0.0.0.0/' /etc/mysql/mariadb.conf.d/50-server.cnf
sudo systemctl restart mariadb
```

### Phase E — Configure K3s

```bash
kubectl create namespace airflow-my-namespace
kubectl config set-context --current --namespace=airflow-my-namespace
mkdir -p ~/airflow/{dags,helm,manifests,dag-mylogs} ~/dashboard/manifests ~/dashboard_build
sudo mkdir -p /opt/airflow/{logs,out} && sudo chown -R ubuntu:ubuntu /opt/airflow
```

From Mac: sync manifests, apply PV/PVC YAMLs, create db-credentials secret in both namespaces, then install Airflow via Helm.

### Phase F — First deploy from Mac

Add temp SSH config entry, run `./scripts/deploy.sh`, test via SSH tunnel.

### Phase G — Verify

All pods Running, Airflow UI loads, DAGs succeed, dashboard shows data, `free -h` < 6 GB, SSH has no post-quantum warning.

### Phase H — Cutover (move Elastic IP)

1. AWS Console → Elastic IPs → Disassociate from old → Associate with new
2. Update `~/.ssh/config` to single `ec2-stock` entry with `User ubuntu`
3. `ssh-keygen -R <EIP>` to clear old host key
4. Verify: `ssh ec2-stock` connects, `./scripts/deploy.sh` works

### Phase I — Cleanup (after 48–72 hours)

Stop old instances as safety net for 1 week. After 1 week: terminate, delete old AMIs/snapshots.

**Success criteria:** DAGs run, dashboard works, deploy.sh succeeds, SSH uses post-quantum KEX.
