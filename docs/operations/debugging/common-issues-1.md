# Common Issues & Fixes (A-I)

Back to [Debugging Index](../DEBUGGING.md) | [Common Issues (J-N)](common-issues-2.md)

---

### A. DAG task fails with `PermissionError` on `/opt/airflow/out`

**Symptoms:** `transform()` or `load()` fails immediately with `PermissionError` on `OutputTextWriter`. `extract()` succeeds (it has no writer).

**Cause:** The PVC-backed host directory (`/home/ubuntu/airflow/dag-mylogs`) is owned by `ubuntu` (UID 1000) but the Airflow pod runs as UID 50000, which has no write access under default 755 permissions.

**Fix (on EC2):**
```bash
chmod 777 /home/ubuntu/airflow/dag-mylogs
```
`deploy.sh` and `bootstrap_ec2.sh` now run this automatically. The fix is also baked into `file_logger.py` — `OutputTextWriter` soft-fails to stdout-only rather than crashing the task if the path isn't writable.

---

### B. Service has `<none>` endpoints → port unreachable

**Symptoms:** Port 30080 or 32147 returns "Connection refused". `kubectl get endpoints` shows `<none>`.

**Cause:** The service's selector labels don't match any running pod's labels. This happened with Airflow 3.x — the webserver pod was renamed from `airflow-webserver` to `airflow-api-server`, but the old NodePort service still had the old selector.

**Diagnose:**
```bash
kubectl describe svc airflow-service-expose-ui-port -n airflow-my-namespace
# Note the "Selector:" line

kubectl get pods -n airflow-my-namespace --show-labels
# Find the Airflow API server pod and check its labels
```

**Fix:** Patch the service selector to match the actual pod labels:
```bash
kubectl patch svc airflow-service-expose-ui-port \
  -n airflow-my-namespace \
  --type='json' \
  -p='[{"op":"replace","path":"/spec/selector/component","value":"api-server"}]'

# Verify endpoints now show an IP:
kubectl get endpoints airflow-service-expose-ui-port -n airflow-my-namespace
```

---

### B. Pod stuck in `ImagePullBackOff`

**Symptoms:** Pod shows `ImagePullBackOff` or `ErrImagePull` in `kubectl get pods`.

**Cause:** The container image can't be pulled. Common reasons:
- Image tag deleted upstream (the Bitnami PostgreSQL incident — `bitnami/postgresql:16.1.0-debian-11-r15` was deleted from Docker Hub)
- ECR token expired (tokens are valid 12 hours; the deploy script refreshes it)
- Wrong image tag or registry URI in the pod spec

**Diagnose:**
```bash
kubectl describe pod <pod-name> -n <namespace>
# Scroll to the "Events:" section — it shows the exact pull error message
```

**Fix (Helm-managed pods like PostgreSQL):**
```bash
# Upgrade the Helm chart — newer chart versions reference updated image paths
helm upgrade --install airflow apache-airflow/airflow \
  --version 1.20.0 \
  -n airflow-my-namespace \
  -f /home/ubuntu/airflow/helm/values.yaml
```

**Fix (Flask pod — ECR token expired):**
```bash
# Re-run deploy.sh from your Mac — Step 5 refreshes the ecr-credentials secret
./scripts/deploy.sh
```

---

### C. Pod stuck in `Init:0/1` or `PodInitializing` forever

**Symptoms:** Airflow pods (scheduler, triggerer, api-server) never reach Running. Show `Init:0/1`.

**Cause:** The `wait-for-airflow-migrations` init container runs before every Airflow pod and blocks until the internal PostgreSQL database is ready. If `airflow-postgresql-0` is broken (e.g., `ImagePullBackOff`), everything else is blocked.

**Diagnose — always check PostgreSQL first:**
```bash
kubectl get pods -n airflow-my-namespace
# Is airflow-postgresql-0 Running? If not, that's the root cause.

# If you want to see what the init container is doing:
kubectl logs <blocked-pod> -n airflow-my-namespace -c wait-for-airflow-migrations
```

Fix the PostgreSQL pod (usually Issue B above), then the blocked pods will unblock on their own.

---

### D. DAG is paused / never runs automatically

**Symptoms:** Airflow is running, DAGs appear in `airflow dags list`, but they never trigger on schedule and nothing happens.

**Cause:** Airflow 3.x defaults `is_paused_upon_creation = True` for all new DAGs. They show up in the UI but won't run until explicitly unpaused.

**Check:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list
# Look for "paused" column — True means it won't run
```

**Fix:**
```bash
# Airflow 3.x uses positional args, not --dag-id flags
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  airflow dags unpause API_Weather-Pull_Data

kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  airflow dags trigger API_Weather-Pull_Data
```

**Check run status:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  airflow dags list-runs API_Weather-Pull_Data
# State goes: queued → running → success (or failed)
```

---

### E. DAG tasks fail with `Access Denied` / DB connection error

**Symptoms:** DAG runs but the `load()` task fails. Error contains `Access Denied` or `Can't connect to MySQL server`.

**Cause:** DB credentials aren't available as environment variables inside the pod. `db_config.py` reads from env vars and defaults to `""` when they're missing.

**Diagnose:**
```bash
# Check if DB env vars are present inside the pod
kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- env | grep -E "DB_|EDGAR_"
# Should show: DB_USER, DB_PASSWORD, DB_HOST, DB_NAME, EDGAR_CONTACT_EMAIL
# If empty or missing, the K8s Secret isn't mounted
```

**Fix — recreate the secret and re-deploy:**

First, verify/set the database password in MariaDB:
```bash
sudo mysql -u root
# In MariaDB:
ALTER USER 'airflow_user'@'10.42.%' IDENTIFIED BY 'YOUR_NEW_PASSWORD';
ALTER USER 'airflow_user'@'<MARIADB_PRIVATE_IP>' IDENTIFIED BY 'YOUR_NEW_PASSWORD';
FLUSH PRIVILEGES;
EXIT;
```

Then create the Kubernetes secret on EC2:
```bash
# EDGAR_CONTACT_EMAIL required — SEC EDGAR User-Agent header reads from this env var
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=YOUR_NEW_PASSWORD \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --dry-run=client -o yaml | kubectl apply -f -

# Also create it in the default namespace for Flask pod:
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=YOUR_NEW_PASSWORD \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --dry-run=client -o yaml | kubectl apply -f -

# Then restart Airflow pods so they pick up the updated secret
kubectl rollout restart statefulset airflow-scheduler -n airflow-my-namespace
kubectl rollout restart deployment airflow-api-server -n airflow-my-namespace
kubectl rollout restart statefulset airflow-triggerer -n airflow-my-namespace

# And restart Flask pod
kubectl delete pod my-kuber-pod-flask -n default
```

Make sure `values.yaml` has this at the **top level** (not nested under `scheduler:` or `triggerer:`):
```yaml
extraEnvFrom: |
  - secretRef:
      name: db-credentials
```

---

### F. Airflow UI unreachable on port 30080

**Diagnose in order:**

1. Is the SSH tunnel active?
   ```bash
   # On your Mac:
   ss -tlnp | grep 30080   # should show the local tunnel listener
   ```
2. Do the Airflow pods have `<none>` endpoints? (Issue A above)
3. Is the api-server pod Running?
   ```bash
   kubectl get pods -n airflow-my-namespace | grep api-server
   ```

---

### G. Dashboard shows empty charts / errors

**Cause:** The `company_financials` table doesn't exist yet — both DAGs create it automatically via `CREATE TABLE IF NOT EXISTS` on the first successful `load()` run.

**Fix:** Trigger both DAGs successfully (Issue D above), then reload the dashboard.

---

### H. `DeprecationWarning: security / rbac / auth_backends is deprecated` when running CLI commands

**Symptoms:** Every time you run an Airflow CLI command (`airflow dags trigger`, `airflow dags list`, etc.) you see a warning like:

```
DeprecationWarning: The 'security' option in section 'core' is deprecated...
# or
DeprecationWarning: The 'rbac' option in section 'webserver' is deprecated...
# or
DeprecationWarning: auth_backends is deprecated, use auth_backend instead
```

**The command still worked.** These are printed to stderr with exit code 0 — the DAG was triggered/unpaused successfully despite the warning.

**Cause:** Airflow 3.x removed several configuration keys that Airflow 2.x used. The Helm chart (installed June 2025) defaulted to the old 2.x-style keys (`[core][security]`, `[webserver][rbac]`, `[api][auth_backends]`). Every CLI invocation sees these stale defaults and warns.

**Verify the command actually worked:**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  airflow dags list-runs API_Weather-Pull_Data
# Should show a row with state: queued → running → success
```

**Fix — none required.** The warning is cosmetic. Ignore it.

> **DO NOT attempt to suppress this by adding a `config:` block to `values.yaml`.** Airflow 3.x removed the FAB (Flask AppBuilder) auth manager entirely. Setting `[core][auth_manager]` to `airflow.auth.managers.fab.fab_auth_manager.FabAuthManager` will crash every Airflow pod in the init container with `ModuleNotFoundError: No module named 'airflow.auth'`. If you do this by mistake, see Issue I below.

---

### I. Pods stuck in `CrashLoopBackOff` after a bad `helm upgrade`

**Symptoms:** A `helm upgrade` timed out or was deployed with a bad config. The upgrade is now fixed and re-run successfully, but some pods (typically `airflow-scheduler-0`, `airflow-triggerer-0`) are still stuck in `CrashLoopBackOff` from the previous bad rollout — even though the new config is correct.

**Cause:** Kubernetes uses exponential backoff for crashing pods (delays grow: 10s, 20s, 40s, 80s...). Pods that were crashing before the fix was deployed are sitting in a long backoff wait and won't pick up the new config until their next restart attempt — which could be several minutes away.

**Fix — force immediate pod recreation:**
```bash
kubectl delete pod airflow-scheduler-0 airflow-triggerer-0 -n airflow-my-namespace
```
Both are StatefulSets, so Kubernetes recreates them instantly. They will start fresh with the current (fixed) config and skip the backoff entirely.

**Verify they come back clean:**
```bash
kubectl get pods -n airflow-my-namespace -w
# -w = watch mode: streams live updates indefinitely — press Ctrl+C to exit
# Watch: Pending → Init:0/1 → Running
```
