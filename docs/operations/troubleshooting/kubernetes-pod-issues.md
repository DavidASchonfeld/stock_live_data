# Kubernetes Pod Issues

Troubleshooting pod crashes, OOMKill, CrashLoopBackOff, Helm upgrade failures, and service routing.

**See also:** [Parent index](../TROUBLESHOOTING.md) | [DEBUGGING.md](../DEBUGGING.md) | [RUNBOOKS.md](../RUNBOOKS.md)

---

## Issue: All Pods `CreateContainerConfigError` After `helm upgrade` (Airflow Major Version)

### Symptoms
- Every pod in `airflow-my-namespace` is in `CreateContainerConfigError` or `Init:CrashLoopBackOff`
- `kubectl describe pod <any-pod>` shows: `Error: secret "airflow-webserver-secret-key" not found`
- No migration job is running or has recently run
- `helm upgrade` keeps timing out

### Why This Happens

This occurs when upgrading from Airflow 2.x to 3.x. The Airflow 2.x Helm chart created a secret named `airflow-webserver-secret-key`. The 3.x chart does NOT create it (replaced by `airflow-api-secret-key`). But the chart's default settings still have `enableBuiltInSecretEnvVars.AIRFLOW__WEBSERVER__SECRET_KEY: true`, which injects that env var — referencing the nonexistent secret — into every pod spec.

Because the migration job pod also has this issue, the migration job never starts. Without the migration, all other pods' init containers wait forever and crash. Everything is blocked by one missing chart default.

### Diagnosis

```bash
# Confirm the secret is missing
ssh ec2-stock "kubectl get secrets -n airflow-my-namespace | grep webserver"
# Should show nothing — airflow-webserver-secret-key doesn't exist in 3.x

# Confirm the pod error
ssh ec2-stock "kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace | grep -A2 'Error:'"
# Expect: Error: secret "airflow-webserver-secret-key" not found

# Check DB migration state (was the migration job ever able to run?)
ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-postgresql-0 -- \
  env PGPASSWORD=postgres psql -U postgres -d postgres \
  -c 'SELECT version_num FROM alembic_version;'"
# If it returns 686269002441 (or another 2.x revision), migration hasn't run yet
```

### Fix

1. Add to `airflow/helm/values.yaml`:
   ```yaml
   enableBuiltInSecretEnvVars:
     AIRFLOW__WEBSERVER__SECRET_KEY: false
   ```

2. Sync and upgrade:
   ```bash
   scp airflow/helm/values.yaml ec2-stock:~/airflow/helm/values.yaml
   ssh ec2-stock "helm upgrade airflow apache-airflow/airflow \
     -n airflow-my-namespace \
     --version 1.20.0 \
     -f ~/airflow/helm/values.yaml \
     --atomic=false --timeout 2m"
   ```

3. If any StatefulSet pods (scheduler-0, triggerer-0) are still stuck with the old spec, force-recreate them:
   ```bash
   ssh ec2-stock "kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace"
   ssh ec2-stock "kubectl delete pod airflow-triggerer-0 -n airflow-my-namespace"
   ```

4. Confirm migration completed:
   ```bash
   ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-postgresql-0 -- \
     env PGPASSWORD=postgres psql -U postgres -d postgres \
     -c 'SELECT version_num FROM alembic_version;'"
   # Expect: 509b94a1042d (Airflow 3.1.8 head)
   ```

---

## Issue: Helm Upgrade Stuck — "another operation (install/upgrade/rollback) is in progress"

### Symptoms
- `helm upgrade` immediately fails with: `Error: UPGRADE FAILED: another operation (install/upgrade/rollback) is in progress`
- The cluster seems idle — no active deploy running

### Why This Happens

A previous `helm upgrade` process was killed (e.g., terminal closed, timeout) while Helm had already written a `pending-upgrade` status secret. Helm uses this to lock against concurrent upgrades. If the process was killed instead of completing, the lock is never released.

### Fix

```bash
# Find the stuck pending-upgrade release
ssh ec2-stock "kubectl get secret -n airflow-my-namespace \
  -l 'owner=helm,name=airflow' \
  -o jsonpath='{range .items[*]}{.metadata.name} {.metadata.labels.status}{\"\\n\"}{end}'"
# Look for a line ending in 'pending-upgrade'

# Delete that specific secret to release the lock
ssh ec2-stock "kubectl delete secret sh.helm.release.v1.airflow.vN -n airflow-my-namespace"
# Replace vN with the actual revision number from the above output

# Also check for and kill any lingering helm process on EC2
ssh ec2-stock "ps aux | grep helm | grep -v grep"
ssh ec2-stock "kill <PID>"  # if a process is still running

# Now retry the upgrade
ssh ec2-stock "helm upgrade airflow apache-airflow/airflow ..."
```

---

## Issue: All Static Assets Fail — "Network Connection Was Lost" (OOMKill)

**Symptoms:**
- Airflow UI loads but has no styling. Browser DevTools shows 10+ simultaneous "network connection was lost" errors for every CSS/JS file (`main.js`, `bootstrap.min.js`, `ab.css`, etc.) — all failing at once.
- **Or:** You navigate away from the UI, come back a few minutes later, and get "server unexpectedly dropped the connection" / SSH tunnel reports `channel N: open failed: connect failed: Connection refused`. The pod OOMKilled while you were away; K3S NodePort has no endpoint to route to.

Both are the same root cause — the api-server pod was OOMKilled. The difference is only in timing: static-asset errors mean you were watching mid-crash; connection-refused means you returned after it already crashed and restarted (or is restarting).

**Root cause:** The api-server pod exceeded its memory limit and was OOMKilled. Kubernetes force-kills the pod; all in-flight HTTP connections drop simultaneously, including the browser's CSS/JS requests. If you see a *single* API endpoint fail, suspect a DAG parse error (see [Fix DAG Parse Errors runbook](../RUNBOOKS.md#16-fix-dag-parse-errors--err_network-on-grid-view)). If *all* static files fail at once, suspect a pod restart.

```bash
# Confirm OOMKill — look for "OOMKilled" in last state (Airflow 3.x: component=api-server, not webserver)
kubectl describe pod -l component=api-server -n airflow-my-namespace | grep -A5 "Last State:"

# Check live memory usage
kubectl top pod -n airflow-my-namespace

# Check restart count — a high count confirms repeated OOMKills
kubectl get pods -n airflow-my-namespace | grep api-server
```

**Fix applied (2026-04-06):** Increased `apiServer` memory limit from `1Gi` → `2Gi` and added `AIRFLOW__API_SERVER__WORKERS=2` in `values.yaml` — same fix used for `webserver` in Airflow 2.x (OOMKilled at 1Gi with 4 gunicorn workers × ~300MB provider load each). If you see this again, check that `values.yaml` wasn't reverted and that `apiServer.resources.limits.memory` is `2Gi`. See [Runbook #17](../RUNBOOKS.md#17-fix-static-assets-failing-oomkill--network-connection-lost).

---

## Issue: Airflow UI (Port 30080) Drops Connection — Service Has No Endpoints

### Symptoms
- `http://localhost:30080` fails immediately: "server unexpectedly dropped the connection"
- SSH tunnel is open and working (dashboard on 32147 loads fine)
- All Airflow pods show `Running` with `0` restarts

### Root Cause: Service Selector Mismatch

The NodePort service routes traffic to pods by matching **labels**. If the selector doesn't match any pod's labels, the service has no endpoints and drops all connections.

Confirm this is the cause:
```bash
kubectl get endpoints -n airflow-my-namespace airflow-service-expose-ui-port
# If ENDPOINTS shows <none>, the selector matches nothing
```

Compare the service selector against the actual pod labels:
```bash
kubectl describe svc -n airflow-my-namespace airflow-service-expose-ui-port | grep Selector
kubectl get pods -n airflow-my-namespace --show-labels
```

**Known instance (2026-04-05):** After upgrading Airflow 2.x → 3.x, `service-airflow-ui.yaml` still had `component: webserver` (the 2.x label). In Airflow 3.x the UI/API pod is labeled `component: api-server` — no `webserver` pod exists. The selector matched zero pods → `<none>` endpoints → connection dropped.

### Solution

1. **Edit `airflow/manifests/service-airflow-ui.yaml`** — set selector to `component: api-server` (Airflow 3.x):
   ```yaml
   selector:
     component: api-server  # Airflow 3.x (was: webserver — 2.x only)
     release: airflow
   ```

2. **Re-apply the manifest on EC2:**
   ```bash
   rsync -avz airflow/manifests/service-airflow-ui.yaml ec2-stock:/home/ubuntu/airflow/manifests/
   ssh ec2-stock 'kubectl apply -f ~/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace'
   ```

3. **Verify endpoints populate:**
   ```bash
   ssh ec2-stock 'kubectl get endpoints -n airflow-my-namespace airflow-service-expose-ui-port'
   # Should show: 10.42.x.x:8080  (not <none>)
   ```

4. **Test the port:**
   ```bash
   ssh ec2-stock 'curl -s -o /dev/null -w "%{http_code}" http://localhost:30080/api/v2/monitor/health'
   # Should return: 200
   ```
