# Failure Mode: `airflow dags list` OOM-Kills Scheduler During DAG Verification (exit 137)

**Date:** 2026-04-11
**Severity:** Non-blocking — deploy succeeds after retry, but scheduler container is killed and restarts

## What Happened

Running `./scripts/deploy.sh` showed the following during the DAG verification step:

```
Verifying DAGs are visible (with retry)...
Defaulted container "scheduler" out of: scheduler, scheduler-log-groomer, wait-for-airflow-migrations (init)
command terminated with exit code 137
  DAG list attempt 1/5 failed — retrying in 10s...
Defaulted container "scheduler" out of: scheduler, scheduler-log-groomer, wait-for-airflow-migrations (init)
error: Internal error occurred: unable to upgrade connection: container not found ("scheduler")
  DAG list attempt 2/5 failed — retrying in 10s...
```

Attempt 3 succeeded and the deploy finished with no errors — the retry loop caught both failures. The Warnings & Errors section showed `(none)`.

## Root Cause

Exit code 137 = SIGKILL (OOM kill). The Linux OOM killer terminated the scheduler container because memory usage exceeded the 2Gi container limit.

The deploy sequence that caused this:

1. `_wait_scheduler_exec()` polls until `kubectl exec ... /bin/true` succeeds. This only confirms exec-readiness — it does not verify that Airflow's internal provider load has finished.
2. `airflow dags list` is called immediately after exec-ready. This spawns a subprocess that imports the full Airflow provider stack.
3. The scheduler's own startup is still loading those same providers at that moment. Both loads run concurrently — the combined memory spike exceeds 2Gi → OOM kill.
4. The container restarts. Attempt 2 sees "container not found" because the container is mid-restart.
5. By attempt 3, the scheduler has finished its startup provider load and `dags list` no longer collides — it succeeds.

This is the same class of problem as the `airflow variables set` OOM kill (see `airflow-exec-oom-kill.md`) — any Airflow CLI command that triggers a full provider import will spike memory if the scheduler is already under load.

## Fix

Two changes to `scripts/deploy/airflow_pods.sh`:

**1. Phase B.6 stabilization delay** — added a 20-second sleep between `_wait_scheduler_exec` and the DAG verification loop. This gives the scheduler time to finish its own startup provider load before `dags list` triggers a second load.

**2. Exit 137 detection in the retry loop** — the retry loop now captures the exit code explicitly. If exit 137 is detected, it prints `OOM kill (exit 137): scheduler ran out of memory` and waits 15 seconds for the container to restart before retrying (instead of the generic 10-second retry message that gives no hint about the real cause).

## Files Changed

- `scripts/deploy/airflow_pods.sh` — added Phase B.6 sleep; made retry loop exit-code-aware with explicit OOM kill message

## How to Verify the Fix Worked

After running `./scripts/deploy.sh`, check for:
1. `Waiting 20s for scheduler provider load to stabilize before DAG check...` printed before the DAG verification loop
2. No `exit code 137` in DAG verification output
3. DAG list succeeds on attempt 1
4. Deploy exits with `(none)` in Warnings & Errors
