# Incident: Deploy OOM Kill Bypassing Retry Logic (April 12, 2026)

## Symptom

Deploy fails with exit code 137 at the "Verifying DAGs are visible" step. The log shows exactly one `command terminated with exit code 137` line, then immediately `DEPLOY FAILED (exit code: 137)`. No retry messages appear ("attempt 2/5", "Waiting 15s..."), even though the verification loop is supposed to retry 5 times.

The Stock and Weather DAGs were also repeatedly failing with no Python traceback, consistent with the scheduler pod being OOM-killed mid-task.

## Root Cause

Two bugs combined to cause the failure.

### Bug 1 (Critical): `set -e` bypassed the retry loop

`deploy.sh` sets `set -euo pipefail`. The DAG verification loop in `airflow_pods.sh` was written as:

```bash
ssh "$EC2_HOST" "kubectl exec ... -- airflow dags list 2>&1"
local exit_code=$?
```

When `ssh` returns exit code 137 (OOM kill), bash's `set -e` fires immediately at the `ssh` line and exits the entire script before `local exit_code=$?` can execute. The retry for-loop never runs its second iteration. This is why no retry messages appear — the script dies on the very first attempt.

### Bug 2 (Contributing): `airflow dags list` is OOM-prone

Running `airflow dags list` inside the scheduler pod forces a full Airflow provider stack load plus parsing of every DAG file, all on top of the already-loaded scheduler process. If the scheduler has concurrent DAG tasks running (e.g., stocks and weather triggered immediately after scheduler restart), the combined memory easily exceeds the scheduler's container limit, producing exit 137.

Airflow 3.x uses a separate `dag-processor` pod for DAG parsing. Making the scheduler also parse DAGs during the health check is redundant and unsafe.

## Fix

### 1. Fixed exit code capture to survive `set -e`

Changed the ssh call from:
```bash
ssh "$EC2_HOST" "kubectl exec ... -- airflow dags list 2>&1"
local exit_code=$?
```

To:
```bash
local exit_code=0
ssh "$EC2_HOST" "kubectl exec ... -- airflow health 2>&1" || exit_code=$?
```

The `|| exit_code=$?` pattern prevents `set -e` from triggering. In bash, the right-hand side of a `||` expression is only evaluated when the left side fails, and the overall expression always succeeds from `set -e`'s perspective. This lets the retry loop actually run all 5 attempts.

### 2. Replaced `airflow dags list` with `airflow health`

`airflow health` checks scheduler connectivity and metadata DB state without loading DAG files or the full provider stack. Memory footprint is roughly 10-20x lighter than `airflow dags list`. Since Airflow 3.x delegates DAG parsing to a separate `dag-processor` pod, verifying scheduler health is sufficient to confirm the infrastructure is ready.

**File changed:** `scripts/deploy/airflow_pods.sh` (Phase C1, lines ~140-175)

## Status

Fixed. The 3Gi scheduler memory limit (applied in `helm/values.yaml` during the April 12 DAG extract OOM incident) was already in place but could not take effect because every deploy was failing at this step. Once the deploy succeeds, the 3Gi limit will be applied and concurrent DAG task OOM kills should stop.

## Related Incidents

- `airflow-dags-list-oom-kill.md` — Prior OOM from `airflow dags list` during startup (fixed with 20s delay)
- `dag-extract-oom-apr12-2026.md` — Concurrent DAG OOM fixed by raising scheduler limit to 3Gi
- `airflow-exec-oom-kill.md` — `airflow variables set` OOM pattern (same root cause: provider stack load inside scheduler pod)
