# Failure Mode Map

A proactive catalog of how each component in this system can fail, why it fails, and what the symptoms look like. Organized by component, ranked by likelihood based on real incidents.

**Navigation:**
- Want to see how failures cascade between components? → [COMPONENT_INTERACTIONS.md](COMPONENT_INTERACTIONS.md)
- Need validation strategies at each pipeline stage? → [DATA_FLOW.md](DATA_FLOW.md)
- Looking for prevention patterns? → [../operations/PREVENTION_CHECKLIST.md](../operations/PREVENTION_CHECKLIST.md)

---

## How to Use This Document

When something breaks, find the component showing symptoms below. Each failure mode includes:
- **Symptoms** — What you observe
- **Root cause** — Why it actually happens
- **Blast radius** — What else breaks as a consequence
- **Real incident?** — Whether this has occurred in this project (with link to incident docs)

---

## Airflow (Scheduler + DAG Processor)

### AF-1: DAG Configuration Drift

| Field | Detail |
|-------|--------|
| **Symptoms** | DAG appears in Airflow UI, then disappears after ~30 seconds. `airflow dags list` shows it intermittently. |
| **Root cause** | Dynamic `start_date` (e.g., `pendulum.now().subtract(days=1)`) evaluates to a different value on every parse cycle (~5s intervals). Airflow detects "configuration changed" and rejects the DAG as invalid. |
| **Blast radius** | Only the affected DAG. Other DAGs continue running. Downstream consumers (Flask dashboard) serve stale data. |
| **Prevention** | Always use fixed past dates: `pendulum.datetime(2025, 3, 29, tz="America/New_York")`. Never use `pendulum.now()` or `datetime.now()` in DAG-level config. |
| **Real incident?** | Yes — 2026-03-31. Stock DAG disappeared repeatedly. See [../incidents/2026-03-31/](../incidents/2026-03-31/). |

### AF-2: DAG Not Discovered (Module Variable Missing)

| Field | Detail |
|-------|--------|
| **Symptoms** | DAG file exists in `/opt/airflow/dags/`, passes syntax check, but doesn't appear in `airflow dags list`. No error messages in scheduler logs. |
| **Root cause** | The `@dag` decorator returns a DAG object when called. If the return value isn't assigned to a **module-level variable**, Airflow's DAG parser can't discover it. `stock_market_pipeline()` runs but the result is discarded. |
| **Blast radius** | Only the affected DAG. Silent failure — no errors anywhere. |
| **Prevention** | Always assign: `dag = stock_market_pipeline()`. Add to `deploy.sh` validation: check that DAG files contain a module-level assignment. |
| **Real incident?** | Yes — 2026-03-30. See [../operations/TROUBLESHOOTING.md](../operations/TROUBLESHOOTING.md). |

### AF-3: Init Container Blocks All Pods

| Field | Detail |
|-------|--------|
| **Symptoms** | All Airflow pods (scheduler, triggerer, api-server) stuck at `Init:0/1` or `PodInitializing` indefinitely. |
| **Root cause** | Every Airflow pod runs a `wait-for-airflow-migrations` init container that blocks until the internal PostgreSQL database is reachable and migrated. If `airflow-postgresql-0` is down (e.g., `ImagePullBackOff` due to deleted Docker Hub image), nothing else can start. |
| **Blast radius** | **Total Airflow outage.** All DAGs stop. No new data ingested. Flask serves stale data. Single dependency (PostgreSQL) cascades to everything. |
| **Prevention** | Pin PostgreSQL image tags to digests, not mutable tags. Monitor PostgreSQL pod health independently. |
| **Real incident?** | Yes — 2026-03-30. Bitnami deleted `postgresql:16.1.0-debian-11-r15` from Docker Hub. See [../incidents/2026-03-30/](../incidents/2026-03-30/). |

### AF-4: XCom Serialization Mismatch

| Field | Detail |
|-------|--------|
| **Symptoms** | Transform task succeeds, load task fails or inserts wrong data. DataFrame columns in load task don't match what transform produced. |
| **Root cause** | XCom serializes task outputs to JSON and deserializes on the receiving end. `DataFrame.to_dict(orient="records")` produces a list of dicts, but if the DataFrame has unexpected structure (e.g., nested objects), the round-trip through JSON changes the shape. |
| **Blast radius** | Data corruption — wrong columns or values inserted into MariaDB. Flask serves garbage data. Silent unless schema validation is in place. |
| **Prevention** | Validate DataFrame schema (column names + types) in both transform output and load input. Assert expected columns before `to_sql()`. |
| **Real incident?** | Yes — Weather DAG `load()` task received wrong column structure. |

### AF-5: DAG Processor Filesystem Cache Stale

| Field | Detail |
|-------|--------|
| **Symptoms** | DAG visible after deploy, disappears after ~90 seconds. `airflow dags reserialize` brings it back temporarily. Scheduler logs show successful parse. |
| **Root cause** | Scheduler and Processor pods both mount the same `hostPath` volume, but each gets its own Linux filesystem cache. After `rsync` updates files on EC2, the Scheduler sees the new inode but the Processor pod retains a cached old directory listing. Airflow's sync cycle queries the Processor's stale view, can't find the file, marks DAG `is_stale: True`. |
| **Blast radius** | Only newly deployed DAGs. Existing DAGs unaffected. |
| **Prevention** | After deploying files, restart both Scheduler AND Processor pods. Or migrate DAGs to ConfigMap-based deployment. |
| **Real incident?** | Yes — 2026-03-31. Stock DAG 90-second staleness cycle. |

---

## Flask / Dash (API + Dashboard)

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

---

## K3s / Kubernetes

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
| **Symptoms** | Pod shows `CrashLoopBackOff` even after deploying a fix via `helm upgrade`. Pod restarts slowly (exponential backoff: 10s→20s→40s→80s…). |
| **Root cause** | K8s exponential backoff doesn't reset when config changes. The pod is waiting out its backoff timer and hasn't attempted to start with the new config yet. |
| **Blast radius** | Delayed recovery. Fix is deployed but not active for minutes. |
| **Prevention** | After `helm upgrade`, always force-delete pods stuck in CrashLoopBackOff: `kubectl delete pod <name>`. StatefulSets recreate immediately with fresh config. |
| **Real incident?** | Yes — 2026-03-30. |

### K8-3: Service Selector Drift

| Field | Detail |
|-------|--------|
| **Symptoms** | Service exists, pod is Running, but `kubectl get endpoints` shows `<none>`. Port unreachable. |
| **Root cause** | Component renamed (Airflow 2→3: `webserver`→`api-server`) but Service manifest still has old selector. Service can't find pods because labels don't match. |
| **Blast radius** | Service completely broken despite everything else being healthy. |
| **Prevention** | After any Helm upgrade or version change, verify endpoints for all services. |
| **Real incident?** | Yes — 2026-03-30. |

### K8-4: Secret Not Propagated to Running Pods

| Field | Detail |
|-------|--------|
| **Symptoms** | Secret updated via `kubectl create secret ... --dry-run=client -o yaml | kubectl apply -f -`, but pods still use old credential values. |
| **Root cause** | K8s injects Secret values as environment variables at pod startup. Updating the Secret object doesn't hot-reload into running pods. |
| **Blast radius** | Pods running with stale credentials. May cause auth failures to MariaDB or APIs. |
| **Prevention** | Always restart pods after Secret updates. Create a checklist: update Secret → restart pods in both namespaces → verify env vars inside pod. |

### K8-5: Single-Node Resource Exhaustion

| Field | Detail |
|-------|--------|
| **Symptoms** | Pods evicted or OOMKilled (`OOMKilled` = Out Of Memory Killed — OS force-killed a pod for exceeding its RAM limit). New pods stuck in `Pending`. `kubectl describe node` shows resource pressure. |
| **Root cause** | All pods share one node's RAM. A memory leak or large DataFrame processing can starve other pods. No resource limits set means one pod can consume everything. |
| **Blast radius** | Cascading evictions. K8s evicts lowest-priority pods first, which may include MariaDB (total data loss if PV not configured) or the scheduler (all DAGs stop). |
| **Prevention** | Set resource requests and limits on all pods. Monitor node memory usage. Identify which pods are critical vs. evictable. |

---

## AWS EC2 / Infrastructure

### EC-1: SSH Unreachable (IP Restriction)

| Field | Detail |
|-------|--------|
| **Symptoms** | `ssh ec2-stock` hangs or times out. EC2 instance is running in AWS console. |
| **Root cause** | Security group restricts SSH to a specific IP address. Working from a new location (different IP) → blocked. |
| **Blast radius** | Total loss of access. Can't deploy, can't debug, can't view logs. |
| **Prevention** | Document the process for updating the security group IP. Keep AWS console access available as backup. |
| **Real incident?** | Recurring — by design (security), but requires awareness when changing locations. |

### EC-2: Disk Full

| Field | Detail |
|-------|--------|
| **Symptoms** | Pods crash with write errors. MariaDB inserts fail. Container image pulls fail. `df -h` shows >95% usage. |
| **Root cause** | Container images, Airflow logs, MariaDB data, and K3s system data all share one EBS volume. No log rotation or image pruning configured. |
| **Blast radius** | Everything fails. Can't write logs, can't pull images, can't insert data. |
| **Prevention** | Monitor `df -h` periodically. Prune old container images (`crictl rmi --prune`). Rotate Airflow logs. Set MariaDB `max_binlog_size`. |

### EC-3: Instance Stopped/Terminated

| Field | Detail |
|-------|--------|
| **Symptoms** | SSH fails. AWS console shows instance in `stopped` or `terminated` state. |
| **Root cause** | AWS maintenance events, billing issues, or accidental stop. K3s doesn't auto-recover gracefully on all restart scenarios. |
| **Blast radius** | Total outage. All services down. Data on EBS volumes preserved (if not terminated). |
| **Prevention** | Enable CloudWatch alarm for instance state changes. Consider reserved instance or savings plan for cost predictability. |

### EC-4: ECR Auth Boundary

| Field | Detail |
|-------|--------|
| **Symptoms** | Image pulls fail with 401 errors. `docker login` or `crictl pull` returns authentication error. |
| **Root cause** | ECR tokens are region-specific and expire after 12 hours. If token isn't refreshed before pod restart, pull fails. |
| **Blast radius** | Any pod that needs to pull an image from ECR. Existing running pods unaffected. |
| **Prevention** | Automate token refresh. `deploy.sh` already handles this — ensure any manual pod restarts also refresh the token first. |

### EC-5: Resource Exhaustion (CPU/Memory)

| Field | Detail |
|-------|--------|
| **Symptoms** | SSH sluggish. Commands timeout. Pods report OOMKilled (Out Of Memory Killed — OS force-killed a pod for exceeding its RAM limit). `top` shows high memory/CPU. |
| **Root cause** | All RAM and vCPU shared across all K3s pods plus the OS. Large DataFrame operations in DAGs, runaway log growth, or memory leaks push past limits. |
| **Blast radius** | Cascading pod evictions. SSH itself may become unusable if the OOM killer targets system processes. |
| **Prevention** | Set K8s resource limits per pod. Monitor with `kubectl top nodes` and `kubectl top pods`. |

---

## API Layer (SEC EDGAR / Open-Meteo)

### API-1: Rate Limit / Quota Exceeded

| Field | Detail |
|-------|--------|
| **Symptoms** | SEC EDGAR: HTTP 403 if exceeding 10 requests/second. Open-Meteo: HTTP 429 if exceeding daily limit. |
| **Root cause** | SEC EDGAR allows 10 req/sec (no daily limit). Open-Meteo: 10,000 requests/day. `edgar_client.py` has a built-in `RateLimiter` class (token-bucket, 8 req/sec) that prevents hitting the SEC limit. |
| **Blast radius** | Extract task fails with HTTP error. No risk of garbage data passing downstream (unlike the old Alpha Vantage setup where rate-limit responses looked like HTTP 200 success). |
| **Prevention** | Rate limiting handled automatically by `RateLimiter` class in `edgar_client.py`. Response structure validated before returning (`facts` and `us-gaap` keys checked). |
| **Real incident?** | Alpha Vantage rate limits caused issues before migration to SEC EDGAR. No SEC EDGAR rate limit incidents since migration. |

### API-2: Schema Change

| Field | Detail |
|-------|--------|
| **Symptoms** | Transform task fails with KeyError or produces DataFrame with unexpected columns. Or succeeds but data is wrong. |
| **Root cause** | API provider changes JSON structure (renamed keys, nested differently, new required fields). Your XBRL concept names or response parsing assumptions break. |
| **Blast radius** | Silent data corruption if new schema partially overlaps old expectations. Loud failure if key paths completely change. |
| **Prevention** | Validate that expected top-level and nested keys exist before `json_normalize()`. Log schema fingerprint (sorted column list) and alert on change. |

### API-3: API Downtime / Timeout

| Field | Detail |
|-------|--------|
| **Symptoms** | Extract task hangs or raises `ConnectionError` / `Timeout`. |
| **Root cause** | External service is down, experiencing high latency, or network path is broken. |
| **Blast radius** | DAG run fails. If no retry configured, data is missing for that interval. |
| **Prevention** | Set explicit `timeout` on all `requests.get()` calls (e.g., 30 seconds). Configure Airflow task retries (2-3 retries with exponential backoff). |

### API-4: Empty / Malformed Response

| Field | Detail |
|-------|--------|
| **Symptoms** | API returns 200 OK but body is empty string, HTML error page, or truncated JSON. |
| **Root cause** | CDN/proxy error, partial response due to connection drop, or API returning error as HTML instead of JSON. |
| **Blast radius** | `json.loads()` fails (loud) or produces empty dict (silent). Downstream tasks get no data. |
| **Prevention** | Validate `Content-Type` header is `application/json`. Check `len(response.text) > minimum_threshold`. Wrap JSON parsing in try/except with informative error message. |

### API-5: Timezone / Date Boundary Issues

| Field | Detail |
|-------|--------|
| **Symptoms** | Requesting "today's" stock data returns empty or partial results. Works fine for historical dates. |
| **Root cause** | DAG scheduled in UTC but market operates in ET. Requesting data before market close (4 PM ET) returns incomplete daily data. Weekends and holidays return no data. |
| **Blast radius** | Missing data for current day. Load task may insert partial row or skip entirely. |
| **Prevention** | Schedule stock DAG after market close (e.g., 5 PM ET / 21:00 UTC). Handle empty results gracefully (log warning, skip insert, don't raise). |

---

## Quick Lookup: "I See This Symptom, What Is It?"

| Symptom | Most likely failure mode |
|---------|------------------------|
| DAG appears then vanishes after ~30s | AF-1 (config drift) |
| DAG appears then vanishes after ~90s | AF-5 (processor cache) |
| DAG never appears, no errors | AF-2 (module variable) |
| All Airflow pods stuck Init:0/1 | AF-3 (PostgreSQL down) |
| Pod shows ImagePullBackOff | FL-2 (ECR token) or AF-3 (deleted image) |
| Port unreachable, pod is Running | K8-3 or FL-5 (selector mismatch) |
| Pod empty directory, files on EC2 | K8-1 (PV path mismatch) |
| Fix deployed but pod still crashing | K8-2 (backoff inertia) |
| SSH timeout from new location | EC-1 (IP restriction) |
| API returns data but it's wrong | API-2 (schema change) |
| Dashboard shows old data, no errors | FL-4 (stale data, silent DAG failure) |

---

**Last updated:** 2026-03-31
