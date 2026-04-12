# Failure Mode: "Airflow DAG verification or variable setup failed" Warning

## Symptom

Deploy completes but the summary block shows:

```
WARNING: Airflow DAG verification or variable setup failed. Check manually.
```

No further detail is printed, and it is not clear whether DAG listing or variable setup was the failing step.

## Root Cause

In `scripts/deploy/airflow_pods.sh`, Phase C of `step_restart_airflow_pods`, two distinct operations were chained in a single SSH call using `&&`:

1. `airflow dags list` — verifies DAGs are visible in the scheduler pod
2. `airflow variables set KAFKA_BOOTSTRAP_SERVERS ...` — writes the Kafka bootstrap address to the Airflow database

Because both commands used `&&`, a failure in *either* triggered the same generic warning with no indication of which step failed or why. The most common cause was a **transient race condition**: `_wait_scheduler_exec` confirms the container accepts `kubectl exec`, but Airflow's internal database connection pool finishes initialising a few seconds later. A `dags list` call in that gap exits non-zero even though the scheduler is healthy, triggering the false warning.

## Fix (applied 2026-04-11)

**File**: `scripts/deploy/airflow_pods.sh`, function `step_restart_airflow_pods`

Split Phase C into two independent blocks:

- **Phase C1 — DAG verification with retry**: polls up to 5 times (10 s apart) so transient DB-not-ready failures heal automatically. Prints the actual `airflow dags list` output (stdout + stderr) on each attempt, and dumps the last 30 scheduler log lines if all 5 attempts fail.
- **Phase C2 — Variable setup**: runs as a separate SSH call with its own warning. On failure, lists existing Airflow variables so you can confirm whether `KAFKA_BOOTSTRAP_SERVERS` was already set from a prior deploy.

## How to Diagnose if the Warning Recurs

### DAG verification warning after 5 retries

The scheduler logs printed automatically will usually contain the root cause. Common reasons:

- **DAG import error** — a Python exception in a DAG file prevents `airflow dags list` from returning cleanly. Fix the DAG, re-deploy with `--dags-only`.
- **Scheduler DB connection failure** — the Airflow metadata DB (MariaDB) is unhealthy. Check: `kubectl get pods -n airflow-my-namespace` and look for the database pod.
- **OOMKilled scheduler** — the scheduler was killed mid-startup by the OOM killer. Check: `kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace | grep -i oom`.

### Variable setup warning

- Run manually: `kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow variables set KAFKA_BOOTSTRAP_SERVERS kafka.kafka.svc.cluster.local:9092`
- If the variable was already set from a prior deploy (shown by `airflow variables list`), the pipeline will work correctly regardless — this warning can be safely ignored in that case.

## Verification After Fix

```bash
# Check that the variable is set
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow variables get KAFKA_BOOTSTRAP_SERVERS
# Expected: kafka.kafka.svc.cluster.local:9092

# Check DAGs are visible
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list
```
