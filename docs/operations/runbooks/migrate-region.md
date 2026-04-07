# Runbook 13: Migrate EC2 to a New Region

> Part of the [Runbooks Index](../RUNBOOKS.md).

**When:** Moving the EC2 instance to a different AWS region (e.g., us-west-2 → us-east-1).

**Prerequisites:** AWS Console access, SSH key `.pem` file, no active DAG runs.

---

### Phase A — Pre-migration (local Mac)

```bash
# 1. Extract public key from .pem (needed to import into new region)
ssh-keygen -y -f /path/to/your-key.pem
# Save the output line (starts with ssh-rsa)

# 2. Document current security group inbound rules from AWS Console
```

### Phase B — Create AMI + copy to target region

1. **Create AMI:** EC2 → select instance → Actions → Image → Create image. Leave "No reboot" unchecked. Wait for "available" (5–20 min).
2. **Copy AMI:** AMIs → select → Actions → Copy AMI → Destination region. Wait for "available" (15–45 min).

> The AMI carries K3S etcd (all K8s Secrets), MariaDB data, Docker images, and `/home/ubuntu/`.

### Phase C — Launch in target region

3. **Import key pair** in target region
4. **Create security group** with identical inbound rules
5. **Create ECR repo** (`my-flask-app`, Private)
6. **Launch instance** from copied AMI — t3.large, attach key pair + security group + **IAM role** (AMIs do NOT copy IAM role)
7. **Allocate + Associate Elastic IP** — EIPs are region-specific and cannot be transferred

### Phase D — First-boot verification

```bash
ssh -i .../your-key.pem ubuntu@<NEW_IP>
sudo systemctl status k3s
kubectl get pods --all-namespaces   # wait 3–5 min
sudo systemctl status mariadb

# Update db-credentials with new private IP
NEW_IP=$(hostname -I | awk '{print $1}')
for NS in airflow-my-namespace default; do
  kubectl create secret generic db-credentials -n $NS \
    --from-literal=DB_USER=airflow_user \
    --from-literal=DB_PASSWORD=<password> \
    --from-literal=DB_HOST=$NEW_IP \
    --from-literal=DB_NAME=database_one \
    --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
    --dry-run=client -o yaml | kubectl apply -f -
done

# Restart pods to pick up new secret
kubectl rollout restart deployment -n airflow-my-namespace
kubectl delete pod my-kuber-pod-flask -n default
```

### Phase E — Update local config

| File | Change |
|------|--------|
| `~/.ssh/config` | `HostName` → new EIP |
| `.env.deploy` | `ECR_REGISTRY` → new region registry; `AWS_REGION` → new region |
| `infra_local.md` | Update EIP, MariaDB private IP, service URLs |

### Phase F — Deploy and test

```bash
./scripts/deploy.sh
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
```

**Pre-deploy:** Verify IAM role is attached (`ssh ec2-stock 'aws sts get-caller-identity'`).

**Post-deploy checklist:**
- All pods Running
- Airflow UI loads
- Both DAGs succeed when triggered manually
- Dashboard shows data
- `free -h` < 6 GB used

### Phase G — Cleanup (after 48–72 hours stable)

- Release old EIP (stops billing)
- Stop old instance as safety net for 1 week
- After 1 week: terminate old instance, delete old AMI + snapshots, delete old ECR repo

**Success criteria:** Both DAGs run clean, dashboard displays data, deploy.sh works, `free -h` < 6 GB.
