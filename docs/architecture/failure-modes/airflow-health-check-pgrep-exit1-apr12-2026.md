# Incident: Airflow Health Check pgrep Exit 1 (Process Not Found) — Apr 12 2026

## What Happened

After fixing the earlier OOM incident (replaced `airflow health` with `curl localhost:8974/health`) and the subsequent exit 7 incident (replaced `curl :8974` with `pgrep -f 'airflow scheduler'`), the health check continued failing — this time with exit 1 on all 5 attempts:

```
Health check attempt 1/5 failed (exit 1) — retrying in 10s...
Health check attempt 2/5 failed (exit 1) — retrying in 10s...
Health check attempt 3/5 failed (exit 1) — retrying in 10s...
Health check attempt 4/5 failed (exit 1) — retrying in 10s...
Health check attempt 5/5 failed (exit 1) — retrying in 10s...
WARNING: airflow health failed after 5 attempts. Scheduler may not be ready — check scheduler logs.
```

The scheduler was running normally — LocalExecutor workers were starting, SchedulerJobRunner was active, DAGs were scheduling correctly. The warning was a false alarm.

## Root Cause

`pgrep` exit 1 means "no processes matched" — the binary is present but the pattern found nothing.

`pgrep -f 'airflow scheduler'` searches all running processes for any whose full command line contains the substring `airflow scheduler`. In Airflow 3.x, the scheduler container's main process does not have that exact string in its command line. The process may run under a launcher (`dumb-init`, entrypoint script), or the Python path format differs, but regardless — the pattern does not match.

This is the third health check failure in one deploy session on Apr 12 2026:

| Attempt | Command | Exit Code | Root Cause |
|---|---|---|---|
| 1 | `airflow health` | 137 (SIGKILL) | OOM: imports full Airflow provider stack (~400 MB), pushes scheduler over 3 Gi limit |
| 2 | `curl localhost:8974/health` | 7 (connection refused) | Port 8974 is Airflow 2.x only — scheduler in 3.x does not expose an HTTP server on that port |
| 3 | `pgrep -f 'airflow scheduler'` | 1 (no match) | Airflow 3.x scheduler process name doesn't contain the expected substring |

## OOM Audit

While investigating, all `kubectl exec` commands in `scripts/deploy/` were audited for OOM risk:

| Command | Target Pod | OOM Risk |
|---|---|---|
| `-- /bin/true` | scheduler | None |
| `-- curl ...` | scheduler | None — no Python |
| `-- pip show / pip install` (ml-venv) | scheduler | None — isolated venv, no Airflow provider import |
| `kafka-topics.sh` | kafka | None — JVM |
| `python3 -c "import sqlite3..."` | mlflow | None — stdlib only |

No remaining OOM-risky exec commands. The 3 Gi memory limit and provider-import-free approach are working.

## Fix

Replaced `pgrep -f 'airflow scheduler'` with `curl -s --max-time 10 -o /dev/null http://localhost:8793/`.

Port 8793 is the Airflow 3.x scheduler's internal execution API server (uvicorn/FastAPI), visible in the scheduler pod logs:

```
INFO:     Uvicorn running on http://:8793 (Press CTRL+C to quit)
```

`curl` without `-f` exits 0 for any HTTP response (200, 401, 404, etc.) and exits 7 only if nothing is listening on that port. If port 8793 is up, the scheduler is running. Zero Python overhead — no provider import, no OOM risk. `curl` is available in the official `apache/airflow:3.1.8` Docker image.

## ASGI Warning (Cosmetic)

The second deploy warning — `WARNING: ASGI app factory detected. Using it, but please consider setting the --factory flag explicitly.` — comes from Airflow 3.x's uvicorn startup internals. Airflow passes the ASGI app as a callable factory and uvicorn suggests using `--factory`. This is not configurable from our deploy scripts or `values.yaml`. It does not affect functionality and can be ignored.

## Files Changed

- `scripts/deploy/airflow_pods.sh` — Phase B.6 comment + Phase C1 health check command (line ~154)

## How to Verify

On the next deploy:
- Health check attempt 1 should exit 0 immediately (port 8793 responds)
- No "airflow health failed" WARNING in deploy output
- No exit 137 (curl adds zero Python overhead)
