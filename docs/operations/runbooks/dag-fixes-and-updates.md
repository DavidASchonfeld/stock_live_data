# Runbooks 16–18: DAG Fixes + OS Updates

> Part of the [Runbooks Index](../RUNBOOKS.md).

---

## 16. Fix DAG Parse Errors / ERR_NETWORK on Grid View

**When:** Airflow UI shows `ERR_NETWORK` on grid view, or DAGs list shows a red "Import Error" badge.

**Root cause:** Module-level code in a DAG file raised an exception at parse time. Airflow re-parses every DAG file every few seconds. Any `raise` outside a `@task` function fires during parsing.

**Rule: Never raise exceptions at DAG module level.** Secret validation, DB connections, and file I/O belong inside `@task` functions.

```bash
# Step 1 — Check for import errors
kubectl logs -n airflow-my-namespace -l component=dag-processor --tail=100 | grep -i "error\|import\|broken"

# Step 2 — List DAGs with parse errors
kubectl exec -n airflow-my-namespace deploy/airflow-scheduler -- airflow dags list-import-errors

# Step 3 — After fixing and redeploying, confirm clean parse
kubectl logs -n airflow-my-namespace -l component=dag-processor --tail=50 | grep -i "error"
```

**Fix pattern:**
```python
# WRONG — runs at parse time
if not os.getenv("DB_PASSWORD"):
    raise RuntimeError("Missing secret")

# CORRECT — runs only when the task executes
@task()
def load(inData):
    import os
    _missing = [k for k in ["DB_USER", "DB_PASSWORD"] if not os.getenv(k)]
    if _missing:
        raise RuntimeError(f"Missing secrets: {_missing}")
```

**Verify:** No red "Import Error" badge in DAGs list. Grid page loads without ERR_NETWORK.

---

## 17. Fix Static Assets Failing (OOMKill → Network Connection Lost)

**When:** Airflow UI loads as a blank/unstyled page. Browser DevTools shows 10+ "network connection was lost" errors for CSS and JS files. All fail simultaneously.

**Root cause:** The webserver pod exceeded its memory limit and was OOMKilled. All open HTTP connections drop at once.

**Diagnosis:**
```bash
kubectl describe pod -l component=webserver -n airflow-my-namespace | grep -A5 -i "oom\|killed"
kubectl get pod -n airflow-my-namespace -l component=webserver
kubectl top pod -n airflow-my-namespace
```

**Fix:** In `airflow/helm/values.yaml`:
```yaml
airflow:
  config:
    AIRFLOW__WEBSERVER__WORKERS: "2"   # reduce from 4 — cuts memory ~50%

webserver:
  resources:
    limits:
      memory: "2Gi"   # raised from 1Gi
```

Then run `./scripts/deploy.sh` (Step 2d runs `helm upgrade` automatically).

**Verify:**
```bash
kubectl exec -n airflow-my-namespace airflow-webserver-0 -- printenv AIRFLOW__WEBSERVER__WORKERS
kubectl describe pod -l component=webserver -n airflow-my-namespace | grep -i oom
```
Reload Airflow UI — all CSS/JS should load.

---

## 18. Apply Ubuntu OS Security Updates

**When:** SSH login banner shows "N updates can be applied" or "System restart required."

```bash
# 1. Check what's pending (optional)
apt list --upgradable

# 2. Update, upgrade, clean, and reboot
sudo apt update && sudo apt upgrade -y && sudo apt clean && sudo reboot
# Connection drops during reboot — expected (~30 sec)

# 3. Reconnect
ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock

# 4. Verify warnings are gone from login banner
```

> **If `apt upgrade` appears frozen:** It may be waiting for a config file prompt that `-y` doesn't auto-answer. Press **Enter** to accept the default.

**ESM / Ubuntu Pro note:** If the banner shows "1 additional security update with ESM Apps" — skip it. It's non-critical and partly marketing. `sudo pro attach` enrolls for free if you ever want it.

**Success criteria:** Login banner no longer shows update count or restart message.
