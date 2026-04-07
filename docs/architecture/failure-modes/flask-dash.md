# Failure Modes — Flask / Dash (API + Dashboard)

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### FL-1: Empty Dashboard (No Data Yet)

| Field | Detail |
|-------|--------|
| **Symptoms** | Dashboard loads but shows empty charts or "no data" errors. Flask API returns empty JSON arrays. |
| **Root cause** | DAGs haven't run successfully yet. Tables either don't exist or have zero rows. `to_sql(if_exists="append")` creates the table on first successful insert. |
| **Blast radius** | User-facing only. No data corruption. |
| **Prevention** | Add a `/health` endpoint that reports data freshness (latest row timestamp). Dashboard should show "awaiting first data load" instead of empty charts. |

### FL-2: ECR Token Expiry

| Field | Detail |
|-------|--------|
| **Symptoms** | Flask pod shows `ImagePullBackOff`. `kubectl describe pod` shows ECR authentication error. |
| **Root cause** | AWS ECR authentication tokens expire after 12 hours. The `ecr-credentials` K8s Secret must be refreshed before pulling images. `deploy.sh` handles this, but if the pod restarts after 12 hours without a deploy, the cached token is stale. |
| **Blast radius** | Flask pod can't start. Dashboard unavailable. Airflow unaffected (uses Helm-managed images). |
| **Prevention** | Automate ECR token refresh via CronJob in K8s, or use ECR credential helper on the node. |

### FL-3: Database Connection Refused

| Field | Detail |
|-------|--------|
| **Symptoms** | Flask pod running but API returns 500 errors. Pod logs show `Can't connect to MySQL server` or `Access denied`. |
| **Root cause** | Either `db-credentials` Secret not mounted in `default` namespace, or MariaDB is down/unreachable. |
| **Blast radius** | All dashboard functionality down. Airflow data ingestion may be unaffected if it's in a different namespace with working credentials. |
| **Prevention** | Flask `/health` endpoint should test DB connectivity. Validate Secret exists in both namespaces after credential rotation. |

### FL-4: Stale Data Served Silently

| Field | Detail |
|-------|--------|
| **Symptoms** | Dashboard appears to work but shows yesterday's (or older) data. No errors visible anywhere. |
| **Root cause** | DAG run failed or didn't trigger. Flask happily serves whatever is in the database. No freshness check. |
| **Blast radius** | User sees outdated information with no indication it's stale. |
| **Prevention** | Add `last_updated` timestamp to API responses. Dashboard should show data age and warn if older than expected interval. |

### FL-5: NodePort Service Selector Mismatch

| Field | Detail |
|-------|--------|
| **Symptoms** | Port 32147 returns "Connection refused" despite Flask pod Running. `kubectl get svc` looks normal. |
| **Root cause** | Service's label selector doesn't match the Flask pod's actual labels. `kubectl get endpoints` shows `<none>`. |
| **Blast radius** | Dashboard completely unreachable. Pod is healthy but isolated from network. |
| **Prevention** | Always check `kubectl get endpoints` after service changes. Include endpoint check in deploy validation. |
