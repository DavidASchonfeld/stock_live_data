# Part 5a: Bug History — Configuration and Infrastructure

> Part of the [Plain English Guide](README.md). For upgrade-related bugs, see [Part 5b](05-bugs-upgrade-and-migration.md).

---

### Bug 1: The Invisible DAG (Configuration Drift)

**What happened:** The Stock DAG would appear in the Airflow UI for about 30 seconds, then vanish. It would keep doing this in a loop — appear, disappear, appear, disappear.

**Why it happened:** Airflow reads your DAG file every 5 seconds to check if anything changed. The original code had:

```python
start_date = pendulum.now().subtract(days=1)  # "yesterday"
```

Every time Airflow reads the file, `pendulum.now()` gives a different answer because time keeps moving. Airflow says "the start_date changed — this DAG is broken" and removes it. Then it reads again, sees a "new" DAG, adds it back... and the cycle repeats.

**The fix:** Replace with a fixed date that never changes:

```python
start_date = pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York")
```

---

### Bug 2: The 90-Second Disappearing Act (Stale Cache)

**What happened:** After fixing Bug 1, the Stock DAG would appear and stay... for exactly 90 seconds. Then disappear again.

**Why it happened:** The Scheduler and Processor pods both look at DAG files but each keeps its own cached picture of the folder. After deploying new files, the Scheduler saw the new file immediately but the Processor was still using its old cache from months ago. Every 90 seconds, the Processor said "never heard of that file" and Airflow hid the DAG.

**The fix:** Restart the Processor pod. When a pod restarts, it reads the folder fresh:
```bash
kubectl delete pod -l component=dag-processor -n airflow-my-namespace
```

**The permanent prevention:** `deploy.sh` now automatically restarts both the Scheduler and Processor pods after every deploy (Step 7).

---

### Bug 3: The Wrong Folder (PV Path Mismatch)

**What happened:** Both DAGs were invisible. Files existed on EC2 but the pods couldn't see them.

**Why it happened:** The PV config pointed to an old folder (`/tmp/airflow-dags/`) but deploy.sh was copying files to `/home/ubuntu/airflow/dags/`. The pod mounted the old, empty folder and saw nothing.

**The fix:** Update the PV to point to the correct folder. Since Kubernetes doesn't allow changes to an existing PV, you have to delete the old PV and PVC and create new ones.

---

### Bug 4: The Missing Variable (DAG Not Discovered)

**What happened:** The DAG file was in the pod, Airflow could read it, but the DAG didn't show up.

**Why it happened:** Airflow's DAG finder looks for a variable that holds a DAG object. The original code was:

```python
stock_market_pipeline()  # Runs the function but throws away the result
```

This is like baking a cake and throwing it in the trash. The function creates a DAG, but since nobody keeps a reference to it, Airflow can't find it.

**The fix:** Save the result to a variable:
```python
dag = stock_market_pipeline()  # Now Airflow can find it
```

---

### Bug 5: Alpha Vantage Rate Limits (API Errors)

**What happened:** The Stock DAG would sometimes fail because the Alpha Vantage API stopped responding with data.

**Why it happened:** The free plan only allows 25 API calls per day. After that, the API still responds with "200 OK" but the body says "you've been rate limited."

**The fix (migration to SEC EDGAR):** Replaced Alpha Vantage with SEC EDGAR — free, no daily limit, no API key needed. The `RateLimiter` class in `edgar_client.py` automatically stays under the 10 req/sec policy.

---

### Bug 6: envsubst Not Found on Apple Silicon

**What happened:** `deploy.sh` failed with `command not found: envsubst` on Apple Silicon Macs (M1/M2/M3).

**Why it happened:** On Intel Macs, `envsubst` is in a standard PATH location. On Apple Silicon, Homebrew installs to `/opt/homebrew/bin/`, which isn't always in the shell's PATH.

**The fix:** Added a fallback — if `envsubst` isn't found, use `sed` instead. Both produce identical output, and `sed` is always available.

---

### Bug 7: PostgreSQL Pod Stuck — Image Not Found on Docker Hub

**What happened:** `airflow-postgresql-0` stayed in `ImagePullBackOff` indefinitely.

**Why it happened:** Bitnami (the company that packages these images) deleted their old versioned tags from Docker Hub. The Helm chart's default `bitnami/postgresql:16.1.0-debian-11-r15` no longer exists.

**The fix:** Override the image to pull from Amazon ECR Public (`public.ecr.aws/bitnami/postgresql:16`). ECR Public has all the Bitnami images, no rate limits, and no authentication needed.

---

### Bug 8: Airflow Webserver CrashLoopBackOff — Startup Probe Too Short

**What happened:** The webserver kept restarting in a loop. Logs showed a "clean" shutdown (exit code 0) — not a real crash.

**Why it happened:** Kubernetes has a **startup probe** that checks if a pod is healthy within a time limit (60 seconds default). On a t3.large, gunicorn takes 30–40 seconds to start, plus another 30–60 seconds to load all Airflow provider packages. The probe killed the pod at 60 seconds, before it finished starting.

**Diagnosed by:** Running `kubectl logs --previous` — the logs showed normal startup followed by `[SIGTERM received] — shutting down` at the 18-second mark. No errors, just an external signal.

**The fix:** Override the startup probe in `values.yaml` to give 180 seconds (18 checks × 10 seconds):
```yaml
webserver:
  startupProbe:
    failureThreshold: 18
    periodSeconds: 10
    timeoutSeconds: 20
```
