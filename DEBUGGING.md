# Debugging Guide — Stock Live Data

A learning-oriented reference for debugging this project's stack: **K3s + Airflow + Flask on EC2**.

---

## 1. Mental Model — How the Stack Connects

Before diving into commands, understand the three-layer path that traffic takes when you open `http://localhost:30080`:

```
Your Mac (SSH tunnel)
  → EC2 NodePort (iptables rule, not a bound socket)
    → K8s Service (matches pods by selector labels)
      → Pod endpoint (the actual running container)
```

**Key things that trip you up:**

- **`ss -tlnp` returns nothing for NodePorts** — k3s uses iptables rules, not bound sockets. The port "exists" in the iptables firewall, not as a listening process. `ss` only shows bound sockets, so it will always look empty for k3s NodePorts.

- **`docker ps` shows `k8s_` prefixed containers** — that means containerd is running the pods, not Docker Compose. You're in Kubernetes. Use `kubectl`, not `docker`.

- **Two namespaces exist in this project:**
  - `airflow-my-namespace` — all Airflow pods (scheduler, api-server, triggerer, postgresql, dag-processor)
  - `default` — Flask/Dash pod (`my-kuber-pod-flask`)

- **kubectl context defaults to `airflow-my-namespace`** on EC2, so commands without `-n` apply there. Use `-n default` or `--all-namespaces` for Flask resources.

---

## 2. Diagnostic Command Sequence

Run these in order when something isn't working. Each one narrows down the problem.

```bash
# Step 1: Are pods actually running?
kubectl get pods --all-namespaces
# Look for: Running (good), ImagePullBackOff / CrashLoopBackOff / Init:0/1 (bad)
# Add -w to stream live updates until Ctrl+C (omit -w for a one-time snapshot)

# Step 2: Do the NodePort services exist?
kubectl get svc --all-namespaces
# Look for: TYPE=NodePort, PORT(S) showing 30080 and 32147

# Step 3: Do the services have endpoints? (THE most important check)
kubectl get endpoints -n airflow-my-namespace
kubectl get endpoints -n default
# "<none>" means the service selector doesn't match any running pod — this is a selector mismatch bug

# Step 4: If endpoints are <none>, what selector is the service looking for?
kubectl describe svc <service-name> -n <namespace>
# Look for: Selector: component=webserver  (or whatever it says)

# Step 5: What labels do the actual pods have?
kubectl get pods -n <namespace> --show-labels
# Compare the selector from Step 4 to the labels here — the mismatch is your bug
```

**The endpoint check (Step 3) is the single most diagnostic command in this stack.** If a port is unreachable, check endpoints before anything else.

---

## 3. Common Issues & Fixes

### A. Service has `<none>` endpoints → port unreachable

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
  -f /home/ec2-user/airflow/helm/values.yaml
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
kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- env | grep DB
# Should show: DB_USER, DB_PASSWORD, DB_HOST, DB_NAME
# If empty or missing, the K8s Secret isn't mounted
```

**Fix — recreate the secret and re-deploy:**

First, verify/set the database password in MariaDB:
```bash
sudo mysql -u root
# In MariaDB:
ALTER USER 'airflow_user'@'10.42.%' IDENTIFIED BY 'YOUR_NEW_PASSWORD';
ALTER USER 'airflow_user'@'172.31.23.236' IDENTIFIED BY 'YOUR_NEW_PASSWORD';
FLUSH PRIVILEGES;
EXIT;
```

Then create the Kubernetes secret on EC2:
```bash
# Replace YOUR_NEW_PASSWORD and YOUR_API_KEY with actual values
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=YOUR_NEW_PASSWORD \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=172.31.23.236 \
  --from-literal=ALPHA_VANTAGE_KEY=YOUR_API_KEY \
  --dry-run=client -o yaml | kubectl apply -f -

# Also create it in the default namespace for Flask pod:
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=YOUR_NEW_PASSWORD \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=172.31.23.236 \
  --from-literal=ALPHA_VANTAGE_KEY=YOUR_API_KEY \
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

**Cause:** The `stock_daily_prices` table doesn't exist yet — both DAGs create it automatically via `CREATE TABLE IF NOT EXISTS` on the first successful `load()` run.

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

> **⚠ DO NOT attempt to suppress this by adding a `config:` block to `values.yaml`.** Airflow 3.x removed the FAB (Flask AppBuilder) auth manager entirely. Setting `[core][auth_manager]` to `airflow.auth.managers.fab.fab_auth_manager.FabAuthManager` will crash every Airflow pod in the init container with `ModuleNotFoundError: No module named 'airflow.auth'`. If you do this by mistake, see Issue I below.

---

### I. Pods stuck in `CrashLoopBackOff` after a bad `helm upgrade`

**Symptoms:** A `helm upgrade` timed out or was deployed with a bad config. The upgrade is now fixed and re-run successfully, but some pods (typically `airflow-scheduler-0`, `airflow-triggerer-0`) are still stuck in `CrashLoopBackOff` from the previous bad rollout — even though the new config is correct.

**Cause:** Kubernetes uses exponential backoff for crashing pods (delays grow: 10s, 20s, 40s, 80s…). Pods that were crashing before the fix was deployed are sitting in a long backoff wait and won't pick up the new config until their next restart attempt — which could be several minutes away.

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

---

## 4. Airflow 3.x Gotchas

If you're reading old StackOverflow answers or Airflow 2.x docs, watch out:

| Area | Airflow 2.x | Airflow 3.x |
|---|---|---|
| Webserver pod name | `airflow-webserver` | `airflow-api-server` |
| CLI flag style | `airflow dags trigger --dag-id foo` | `airflow dags trigger foo` (positional) |
| Service selector | `component: webserver` | `component: api-server` |
| Default DAG state | Unpaused (runs on schedule) | Paused (must manually unpause) |
| Auth config key | `[api][auth_backends]` (plural), `[webserver][rbac]`, `[core][security]` | Old keys print DeprecationWarning on every CLI call — harmless, ignore them. Do NOT set `[core][auth_manager]` to the old FAB class; FAB was removed from Airflow 3.x and doing so crashes all pods (Issue I) |
| Helm upgrade warnings | N/A | `dags.gitSync.recommendedProbeSetting` deprecation: irrelevant if not using git-sync. "Dynamic API secret key" warning: harmless for dev; fix later by adding `webserverSecretKey` to `values.yaml` |

Old NodePort services created when Airflow 2.x was installed will have the wrong selector. The service itself looks fine (`kubectl get svc`) but `kubectl get endpoints` reveals the mismatch.

---

## 5. Reading Logs

```bash
# Last 50 lines of a pod's logs
kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50

# Follow logs live
kubectl logs -f airflow-scheduler-0 -n airflow-my-namespace

# Logs for a specific container in a multi-container pod
kubectl logs airflow-scheduler-0 -n airflow-my-namespace -c scheduler

# Init container logs (useful for Init:0/1 debugging)
kubectl logs airflow-scheduler-0 -n airflow-my-namespace -c wait-for-airflow-migrations

# Exec into a pod for interactive inspection
kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- bash

# Check a task's output log from inside the scheduler pod
airflow tasks logs <dag_id> <task_id> <run_id>

# Flask/Dash pod logs
kubectl logs my-kuber-pod-flask -n default
kubectl logs -f my-kuber-pod-flask -n default
```

---

## 6. Quick Health Check

Run this sequence after any change to confirm everything is healthy:

```bash
# 1. All pods running?
kubectl get pods --all-namespaces

# 2. Services have endpoints? (not <none>)
kubectl get endpoints -n airflow-my-namespace
kubectl get endpoints -n default

# 3. SSH tunnel active on your Mac?
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
# Then: http://localhost:30080 (Airflow UI)
#       http://localhost:32147/dashboard/ (Flask/Dash)
```

Expected healthy state:

| Namespace | Endpoints expected |
|---|---|
| `airflow-my-namespace` | `airflow-service-expose-ui-port` → `10.42.x.x:8080` |
| `default` | `flask-service-expose-port` → `10.42.x.x:5000` |

---

## 7. Related Docs

- `docs/airflow-fix-2026-03-30.md` — full write-up of the three cascading issues fixed (values.yaml sync, Bitnami image deletion, DB credentials injection)
- `docs/refactor-ecr-migration.md` — why containerd + ECR replaced the old `--docker` mode
- `OVERVIEW.md` — full architecture, deploy instructions, production status
- `infra_local.md` — real IPs, credentials, and NodePort values (gitignored)
