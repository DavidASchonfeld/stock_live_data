# Part 5b: Bug History — Upgrade and Migration

> Part of the [Plain English Guide](README.md). For configuration/infrastructure bugs, see [Part 5a](04-bugs-config-and-infra.md).

---

### Bug 9: Triggerer OOMKilled — 256Mi Memory Limit Too Low

**What happened:** `airflow-triggerer-0` kept restarting. Status showed `OOMKilled` (Out Of Memory Killed).

**What OOMKilled means:** The Linux kernel forcibly killed the process because it exceeded its memory limit. Kubernetes sets a ceiling; the kernel enforces it instantly — no warning, the process just disappears.

**Why it happened:** The triggerer's memory limit was 256MB. At startup, loading all Airflow provider packages temporarily pushed past 256MB, and the kernel killed it before it finished starting.

**The fix:** Increase the memory limit in `values.yaml` to `512Mi`. The triggerer settles back to ~100MB in steady state — the extra headroom just handles the startup spike.

---

### Bug 10: deploy.sh Fails — "No module named airflow"

**What happened:** Running `./scripts/deploy.sh` failed at the pre-flight check with: `ModuleNotFoundError: No module named 'airflow'`.

**Why it happened:** `deploy.sh` validates DAG files by importing them with whatever `python3` is on your system PATH. Your system Python doesn't have Airflow installed — it lives in the project's virtual environment (`airflow_env/`).

**The fix:** Activate the project venv before running deploy:
```bash
export PATH="/path/to/data_pipeline/airflow_env/bin:$PATH"
./scripts/deploy.sh
```

This only matters on your Mac. The actual pipeline runs inside Kubernetes pods where Airflow is always available.

---

### Bug 11: Airflow UI (Port 30080) Not Reachable — Service Selector Mismatch

**What happened:** The Flask dashboard loaded fine but the Airflow UI dropped the connection immediately. All pods showed `Running`.

**Why it happened:** Kubernetes services find pods using **labels** (key-value tags). The Airflow service had `component: api-server` as its selector, but the cluster was running Airflow 2.x, which uses `component: webserver`. The service found zero matching pods — so connections were refused.

**How it was diagnosed:**
```bash
kubectl get endpoints -n airflow-my-namespace
# airflow-service-expose-ui-port showed <none> — the tell
```

**The fix:** Changed the selector in `service-airflow-ui.yaml` from `api-server` to `webserver`. Endpoints populated immediately.

**The bigger lesson:** When a port is unreachable but the pod is healthy, check the service endpoints first. If they show `<none>`, the service's selector doesn't match any pod labels.

---

### Bug 12: ERR_NETWORK on the Airflow Grid View — Module-Level raise in a DAG File

**What happened:** The Airflow grid view showed `ERR_NETWORK` in the browser console. The page loaded but the grid was blank.

**Why it happened:** Airflow re-reads every DAG file every few seconds. Python runs the top-level code in the file during parsing. Both DAG files had a secret-validation block at module level:

```python
_missing_secrets = [k for k in _required_secrets if not os.getenv(k)]
if _missing_secrets:
    raise RuntimeError(f"Missing Kubernetes secrets...")
```

When secrets weren't mounted yet at startup, this `RuntimeError` fired during parsing, the DAG failed to load, and the API server dropped the HTTP connection.

**The fix:** Moved the secret validation inside the `@task` function, where it only runs at execution time — not during parsing.

**The bigger lesson:** The top level of your DAG file should only define structure. Never put I/O, network calls, or anything that can raise an exception at module level. Anything that might fail belongs inside a `@task` function.

---

### Bug 13: All Static Assets Fail — Webserver OOMKilled

**What happened:** The Airflow UI showed a blank, unstyled page. Every CSS and JS file failed with "network connection was lost."

**Why it happened:** 4 gunicorn workers × ~300 MB each = ~1.2 GB. The memory limit was 1 GB. Kubernetes killed the pod mid-page-load, dropping all open HTTP connections at once.

**The fix (two parts):**
1. Increase memory limit from 1 GB to 2 GB
2. Reduce gunicorn workers from 4 to 2 (via `AIRFLOW__WEBSERVER__WORKERS: "2"`)
3. Add `helm upgrade` step to deploy.sh so values.yaml changes take effect

---

### Bug 14: The Accidental Upgrade — Running `helm upgrade` Without a Version Pin

**What happened:** A `helm upgrade` without a version pin pulled the latest chart — jumping from Airflow 2.9.3 to 3.1.8. The database got upgraded to the new format before the upgrade timed out, making rollback impossible.

**Why it happened:** `helm upgrade` without `--version` means "give me the latest." The migration job ran first and upgraded the database schema. Then the rest timed out. The cluster was stuck pointing forward.

**The lesson:** Always use `--version` when running `helm upgrade` in production.

---

### Bug 15: Every Pod Crashed — Missing Secret After Upgrade

**What happened:** After the Airflow 3.x upgrade, every `helm upgrade` attempt timed out. Every pod showed `CreateContainerConfigError` or `Init:CrashLoopBackOff`.

**Why it happened:** The chart referenced a secret called `airflow-webserver-secret-key` (a 2.x thing). In 3.x, this was replaced with `airflow-api-secret-key`. The old secret didn't exist, so every pod failed to start — including the migration job, which meant the database never migrated, which meant init containers waited forever.

**The chain:**
```
Chart references non-existent secret → every pod can't start → migration job can't start
→ database never migrated → init containers wait forever → all pods crash → helm times out
```

**The fix:** Add one setting to `values.yaml`:
```yaml
enableBuiltInSecretEnvVars:
  AIRFLOW__WEBSERVER__SECRET_KEY: false
```

---

### Bug 16: Scheduler Kept Dying — Memory and Probe Limits From the 2.x Era

**What happened:** After fixing Bug 15, the scheduler started up then crashed after ~3 minutes, repeatedly. Two different things killed it: OOMKilled first, then probe timeouts.

**Why the OOMKill:** Airflow 3.x spawns ~15 worker processes at startup (2.x used one). Each loads all provider packages (~80–100 MB each). The old 1 GB limit couldn't handle it.

**Why the probe timeout:** The health check command (`airflow jobs check`) loads the full Airflow codebase before responding. In 3.x this takes 30–45 seconds. The old timeout was 20 seconds — so the check always "failed."

**The fix:** Raise scheduler memory to 2 GB and probe timeout from 20s to 45s:
```yaml
scheduler:
  startupProbe:
    timeoutSeconds: 45
  livenessProbe:
    timeoutSeconds: 45
```

**The lesson:** When upgrading major versions, the old sizing values were calibrated for the old architecture. Airflow 3.x is more heavyweight than 2.x — recalibrate limits.
