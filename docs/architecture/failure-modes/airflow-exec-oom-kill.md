# Failure Mode: `airflow variables set` OOM-Kills Scheduler (exit 137)

## What Happened

Deploy failed at step 7c ("Setting Airflow Variable: MLFLOW_TRACKING_URI") with:

```
command terminated with exit code 137
```

Exit code 137 means the process was killed by SIGKILL — not a timeout, not a crash. The kernel or Kubernetes forcibly terminated it.

The same failure also existed for `KAFKA_BOOTSTRAP_SERVERS` set in `step_restart_airflow_pods`.

Previous Claude attempts focused on increasing timeouts and CPU requests. Those were unrelated to the actual problem.

## Root Cause

Two places in the deploy ran `kubectl exec airflow-scheduler-0 -- airflow variables set <NAME> <VALUE>`.

On Airflow 3.x, the `airflow variables set` CLI command is not a lightweight operation. It:

1. Spawns a full Python subprocess inside the scheduler container
2. Imports the entire Airflow module and provider ecosystem (Snowflake, Kafka, OpenLineage, etc.)
3. Opens a database connection to write the variable
4. Exits

Step 2 is the problem. Importing all providers on Airflow 3.x uses several hundred MB of RAM. The scheduler container was already under memory pressure from its normal work (DAG parsing, LocalExecutor task supervision, provider loading). This additional spike pushed it past its 2Gi memory limit, causing the Linux kernel OOM-killer to send SIGKILL to the process. `kubectl exec` reports this as exit code 137.

It could also happen if the Kubernetes liveness probe restarted the scheduler container while the exec session was in progress — that also delivers exit 137 to kubectl.

The deploy had been running for 17+ minutes by the time this step executed, so the scheduler had been doing real work and memory was not at baseline.

## Fix

Airflow has a built-in feature: any environment variable named `AIRFLOW_VAR_<NAME>` is automatically exposed as `Variable.get('<NAME>')` to DAG code — no database write, no CLI subprocess, no provider import needed.

Added to `airflow/helm/values.yaml`:

```yaml
- name: AIRFLOW_VAR_MLFLOW_TRACKING_URI
  value: "http://mlflow.airflow-my-namespace.svc.cluster.local:5500"
- name: AIRFLOW_VAR_KAFKA_BOOTSTRAP_SERVERS
  value: "kafka.kafka.svc.cluster.local:9092"
```

Removed the `kubectl exec airflow variables set` blocks from:
- `scripts/deploy/mlflow.sh` (was setting MLFLOW_TRACKING_URI)
- `scripts/deploy/airflow_pods.sh` (was setting KAFKA_BOOTSTRAP_SERVERS)

The env var values are now baked into the Helm release and injected at container start — before any Python code runs, before any DB connection is made.

## Why This Is Better Long-Term

- **No timing dependency**: values are available the instant the container starts
- **No exec fragility**: no kubectl exec session that can fail mid-deploy
- **No memory spike**: no provider imports triggered during deploy
- **Survives DB resets**: env vars don't depend on anything in the Airflow metadata DB
- **Idempotent**: Helm upgrade re-applies them on every deploy automatically
