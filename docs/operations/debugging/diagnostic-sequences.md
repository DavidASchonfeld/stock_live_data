# Diagnostic Sequences, Gotchas & Log Reading

Back to [Debugging Index](../DEBUGGING.md) | [Common Issues (A-F)](common-issues-1.md) | [Common Issues (G-N)](common-issues-2.md)

---

## Diagnostic Command Sequence

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

## Airflow 3.x Gotchas

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

## Reading Logs

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

## Quick Health Check

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

## Related Docs

- `docs/airflow-fix-2026-03-30.md` — full write-up of the three cascading issues fixed (values.yaml sync, Bitnami image deletion, DB credentials injection)
- `docs/refactor-ecr-migration.md` — why containerd + ECR replaced the old `--docker` mode
- `OVERVIEW.md` — full architecture, deploy instructions, production status
- `infra_local.md` — real IPs, credentials, and NodePort values (gitignored)
