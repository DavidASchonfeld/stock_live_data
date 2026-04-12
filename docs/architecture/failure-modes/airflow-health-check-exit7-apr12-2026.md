# Incident: Airflow Health Check Exit 7 (Connection Refused on Port 8974) — Apr 12 2026

## What Happened

After applying the OOM fix from earlier today (replaced `airflow health` with `curl localhost:8974/health`), the very next deploy showed the health check failing on all 5 attempts with exit code 7:

```
command terminated with exit code 7
  Health check attempt 1/5 failed (exit 7) — retrying in 10s...
  Health check attempt 2/5 failed (exit 7) — retrying in 10s...
  Health check attempt 3/5 failed (exit 7) — retrying in 10s...
  WARNING: airflow health failed after 5 attempts. Scheduler may not be ready — check scheduler logs.
```

The scheduler itself was running fine — all pods were Ready, DAGs were scheduling normally. The warning was a false alarm.

## Root Cause

Exit code 7 from curl means "Failed to connect to host" — nothing is listening on the target address.

Port 8974 was the **Airflow 2.x** scheduler's built-in internal HTTP health server. In **Airflow 3.x** (this project runs 3.1.8), the scheduler no longer exposes an HTTP server on that port. The Airflow 3.x Helm chart uses an exec-based startup/liveness probe (`airflow jobs check --job-type SchedulerJob`) rather than an `httpGet` probe — so port 8974 is never opened.

No amount of waiting or retrying would fix this: the port simply does not exist in Airflow 3.x.

This was a latent problem introduced alongside the OOM fix — the new `curl` command inherited the port assumption from Airflow 2.x without validating it against the running Airflow version.

## Fix

Replaced `curl -sf http://localhost:8974/health` with `pgrep -f 'airflow scheduler'` inside the scheduler container.

- `pgrep` checks whether the Airflow scheduler process is running by matching its command line
- Zero Python overhead — no provider import, no OOM risk
- Available in the official Airflow Debian-based Docker image
- Exits 0 if the process is found, non-zero if not — same retry loop behavior as before
- Also removed the 10s stabilization sleep before the check — it was only needed to wait for the HTTP server to bind, which is no longer relevant

## Files Changed

- `scripts/deploy/airflow_pods.sh` — Phase B.6 comment + Phase C1 health check command

## How to Verify

On the next deploy:
- Health check attempt 1 should exit 0 immediately (scheduler process found)
- No "airflow health failed" WARNING in deploy output
- Deploy completes cleanly
