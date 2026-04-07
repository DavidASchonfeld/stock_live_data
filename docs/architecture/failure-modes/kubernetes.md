# Failure Modes — K3s / Kubernetes

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### K8-1: PV/PVC Path Mismatch

| Field | Detail |
|-------|--------|
| **Symptoms** | Pod mounts an empty directory. Files exist on EC2 host but not visible inside the pod. No errors in pod events. |
| **Root cause** | `deploy.sh` syncs files to one path on EC2 (e.g., `/home/ubuntu/airflow/dags/`). PV manifest's `hostPath.path` points to a different path (e.g., `/tmp/airflow-dags/`). K8s silently mounts the wrong (empty) directory. |
| **Blast radius** | All pods using that PV see empty directory. DAGs invisible to Airflow. |
| **Prevention** | Add validation to `deploy.sh`: read PV manifest's `hostPath.path` and compare to sync target. Fail deploy if they diverge. |
| **Real incident?** | Yes — 2026-03-31. |

### K8-2: CrashLoopBackOff Inertia

| Field | Detail |
|-------|--------|
| **Symptoms** | Pod shows `CrashLoopBackOff` even after deploying a fix via `helm upgrade`. Pod restarts slowly (exponential backoff: 10s, 20s, 40s, 80s...). |
| **Root cause** | K8s exponential backoff doesn't reset when config changes. The pod is waiting out its backoff timer and hasn't attempted to start with the new config yet. |
| **Blast radius** | Delayed recovery. Fix is deployed but not active for minutes. |
| **Prevention** | After `helm upgrade`, always force-delete pods stuck in CrashLoopBackOff: `kubectl delete pod <name>`. StatefulSets recreate immediately with fresh config. |
| **Real incident?** | Yes — 2026-03-30. |

### K8-3: Service Selector Drift

| Field | Detail |
|-------|--------|
| **Symptoms** | Service exists, pod is Running, but `kubectl get endpoints` shows `<none>`. Port unreachable. |
| **Root cause** | Component renamed (Airflow 2 to 3: `webserver` to `api-server`) but Service manifest still has old selector. Service can't find pods because labels don't match. |
| **Blast radius** | Service completely broken despite everything else being healthy. |
| **Prevention** | After any Helm upgrade or version change, verify endpoints for all services. |
| **Real incident?** | Yes — 2026-03-30. |

### K8-4: Secret Not Propagated to Running Pods

| Field | Detail |
|-------|--------|
| **Symptoms** | Secret updated via `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`, but pods still use old credential values. |
| **Root cause** | K8s injects Secret values as environment variables at pod startup. Updating the Secret object doesn't hot-reload into running pods. |
| **Blast radius** | Pods running with stale credentials. May cause auth failures to MariaDB or APIs. |
| **Prevention** | Always restart pods after Secret updates. Create a checklist: update Secret, restart pods in both namespaces, verify env vars inside pod. |

### K8-5: Single-Node Resource Exhaustion

| Field | Detail |
|-------|--------|
| **Symptoms** | Pods evicted or OOMKilled (OOMKilled = Out Of Memory Killed — OS force-killed a pod for exceeding its RAM limit). New pods stuck in `Pending`. `kubectl describe node` shows resource pressure. |
| **Root cause** | All pods share one node's RAM. A memory leak or large DataFrame processing can starve other pods. No resource limits set means one pod can consume everything. |
| **Blast radius** | Cascading evictions. K8s evicts lowest-priority pods first, which may include MariaDB (total data loss if PV not configured) or the scheduler (all DAGs stop). |
| **Prevention** | Set resource requests and limits on all pods. Monitor node memory usage. Identify which pods are critical vs. evictable. |

### K8-6: Webserver OOMKill Cascades to "Network Connection Was Lost"

| Field | Detail |
|-------|--------|
| **Symptoms** | Airflow UI loads but ALL CSS/JS static assets fail simultaneously with "network connection was lost". Browser DevTools shows 10+ identical errors at once (main.js, bootstrap.min.js, ab.css, etc.). Page may render as unstyled HTML. |
| **Root cause** | Webserver pod exceeds its memory limit, Kubernetes force-kills the pod (OOMKill), pod restarts, all open HTTP connections drop at once. The browser had already received the page HTML but the CSS/JS downloads were cut off mid-transfer. |
| **Blast radius** | Airflow UI unusable (no styles or JavaScript). Affects all browser sessions during the restart window (~30-90 seconds). No data loss — DAGs keep running; only the UI is disrupted. |
| **Prevention** | Keep webserver memory limit at 2 Gi. Keep `AIRFLOW__WEBSERVER__WORKERS=2` (set via `webserver.env` in `values.yaml`). Ensure `helm upgrade` is run after any `values.yaml` change — syncing the file to EC2 alone does NOT apply changes to running pods. |
| **Real incident?** | Yes — 2026-04-05. Root cause: 4 workers x ~300 MB = ~1.2 Gi exceeded the 1 Gi limit. Additionally, `AIRFLOW__WEBSERVER__WORKERS` was nested under `airflow.config` (silently ignored by the chart) instead of `webserver.env` (the correct key). |
