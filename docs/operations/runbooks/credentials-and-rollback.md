# Runbooks 3–4: Rotate Credentials + Rollback Helm Upgrade

> Part of the [Runbooks Index](../RUNBOOKS.md).

---

## 3. Rotate Database Credentials

**When:** Changing MariaDB password for security or after a suspected compromise.

**Steps:**

```bash
# 1. Update password in MariaDB (on EC2)
ssh ec2-stock
sudo mysql -u root
ALTER USER 'airflow_user'@'10.42.%' IDENTIFIED BY 'NEW_PASSWORD_HERE';
ALTER USER 'airflow_user'@'<MARIADB_PRIVATE_IP>' IDENTIFIED BY 'NEW_PASSWORD_HERE';
FLUSH PRIVILEGES;
EXIT;

# 2. Update K8s Secret in airflow namespace
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=NEW_PASSWORD_HERE \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Update K8s Secret in default namespace (for Flask)
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=NEW_PASSWORD_HERE \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --dry-run=client -o yaml | kubectl apply -f -

# 4. Restart ALL pods (secrets don't hot-reload)
kubectl rollout restart statefulset airflow-scheduler -n airflow-my-namespace
kubectl rollout restart deployment airflow-api-server -n airflow-my-namespace
kubectl rollout restart statefulset airflow-triggerer -n airflow-my-namespace
kubectl delete pod my-kuber-pod-flask -n default

# 5. Wait and verify
sleep 60
kubectl get pods --all-namespaces

# 6. Verify credentials work
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- env | grep DB_PASSWORD

# 7. Test end-to-end
kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  airflow dags trigger Stock_Market_Pipeline

# 8. Update local reference — edit infra_local.md (gitignored)
```

**Success criteria:** All pods running with new credentials, DAG run succeeds, dashboard loads.

---

## 4. Rollback a Bad Helm Upgrade

**When:** A `helm upgrade` broke something and you need to revert.

**Steps:**

```bash
# 1. Check Helm release history
ssh ec2-stock helm history airflow -n airflow-my-namespace
# Note the REVISION number of the last working version

# 2. Rollback to previous revision
ssh ec2-stock helm rollback airflow <PREVIOUS_REVISION> -n airflow-my-namespace

# 3. Force-delete any pods stuck in CrashLoopBackOff
ssh ec2-stock kubectl delete pod airflow-scheduler-0 airflow-triggerer-0 -n airflow-my-namespace

# 4. Wait for pods to stabilize
sleep 60
ssh ec2-stock kubectl get pods -n airflow-my-namespace

# 5. Verify Airflow UI is accessible (http://localhost:30080 via SSH tunnel)

# 6. Verify DAGs are visible
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list

# 7. Check endpoints — must show IPs, not <none>
ssh ec2-stock kubectl get endpoints -n airflow-my-namespace
```

**Success criteria:** All pods Running, Airflow UI accessible, DAGs visible.
