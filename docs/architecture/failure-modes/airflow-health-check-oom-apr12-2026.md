# Incident: Airflow Scheduler OOM Kill During Deploy Health Check (Apr 12 2026)

## What Happened

After a full deploy, the `step_restart_airflow_pods` step consistently OOM-killed the Airflow scheduler pod during the health check phase. The deploy output showed:

```
Health check attempt 1/5 — OOM kill (exit 137): scheduler ran out of memory. Waiting 15s for restart...
```

All 5 retry attempts failed the same way. The scheduler itself was otherwise healthy — it would come back up after each kill and resume scheduling tasks normally.

## Root Cause

The health check ran:
```bash
kubectl exec airflow-scheduler-0 -- airflow health
```

Running `airflow health` via `kubectl exec` spawns a **new Python process inside the scheduler container**. That process imports the full Airflow CLI module tree — including all installed providers (Apache, Snowflake, OpenLineage, etc.) — which adds roughly 300–500 MB of extra memory inside the container.

The scheduler itself already uses 2.5–3 Gi at steady state (providers loaded, OpenLineage subprocesses, task workers). Adding another 300–500 MB from the health check process pushed the container over its 3 Gi cgroup limit. The Linux kernel OOM killer then killed the **entire container** (not just the health check process), resulting in exit 137 (SIGKILL).

This is the same class of problem as the `airflow variables set` OOM kill from Thread 1 — any Airflow CLI command exec'd into the scheduler pod carries this risk when the scheduler is already near its memory ceiling.

## Fix

Replaced `airflow health` with `curl -sf http://localhost:8974/health` in `scripts/deploy/airflow_pods.sh`.

Port 8974 is the scheduler's built-in internal HTTP health server. It returns JSON like:
```json
{"metadatabase": {"status": "healthy"}, "scheduler": {"status": "healthy"}}
```

`curl` adds zero Python overhead — it does not load the Airflow provider stack at all. This is also the exact same endpoint that K8s startup and liveness probes use, so by the time the pod is Ready, the endpoint is guaranteed to be up.

The stabilization wait before the health check was also reduced from 20s to 10s, since we no longer need to wait for provider loading to finish — we just need the HTTP server to bind, which happens as part of normal scheduler startup.

## Files Changed

- `scripts/deploy/airflow_pods.sh` — Phase B.6 comment + Phase C1 health check command

## How to Verify

On the next deploy (full or `--dags-only`):
- The health check step should print the JSON health response on attempt 1 and exit cleanly
- No exit 137 should appear
- The scheduler pod should remain Running throughout the deploy without restarting
