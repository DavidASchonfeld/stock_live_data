# Failure Mode: Step 7c MLflow Reset OOM Kill (Apr 11, 2026)

## Symptom

`./scripts/deploy.sh` failed at **Step 7c: Resetting MLflow experiment artifact root** with:

```
command terminated with exit code 137
```

The deploy reported no WARNING/ERROR keywords, making it look like a silent crash.

## Root Cause

`step_fix_mlflow_experiment()` in `scripts/deploy/mlflow.sh` used to run two sequential commands:

1. **Scheduler pod** — a Python diagnostic that imported `mlflow` and `mlflow.entities.ViewType` to print the current artifact_location state before fixing it.
2. **MLflow pod** — a lightweight `sqlite3` UPDATE to fix the artifact_location directly in the database.

Command 1 was the problem. Importing the full mlflow library inside the Airflow scheduler container (already near its 2 Gi memory limit from normal operation) spiked 500–800 MB and triggered the kernel OOM killer. The actual fix in Command 2 never ran.

This is the same false-failure pattern as the ml-venv health check OOM from earlier the same day: a Python import used for diagnostics/checks inside the scheduler pod → exceeds 2 Gi → exit 137 → misleads the deploy into thinking the step failed.

## Why It Was Hard to Spot

The deploy log showed no WARNING or ERROR lines — only the exit 137. The error reporting logic looked for those keywords. Because the OOM kill is a kernel-level signal, not a Python exception, nothing printed "Error" before dying.

## Fix

Removed the scheduler-pod Python diagnostic check entirely. The sqlite3 command in the MLflow pod already:

- Reads and prints the current artifact_location and lifecycle_stage before changing anything
- Skips with a clear message if the state is already correct
- Is fully idempotent

Step 7c now goes directly: wait for MLflow rollout → run sqlite3 fix in MLflow pod. No scheduler pod exec, no mlflow imports, no OOM risk.

**File changed:** `scripts/deploy/mlflow.sh` — `step_fix_mlflow_experiment()`

## Pattern to Avoid

Never run `import mlflow`, `import sklearn`, or other heavy ML libraries via `kubectl exec` inside the Airflow scheduler pod. The scheduler container runs near its 2 Gi memory limit at all times. Even a one-off Python import for diagnostic purposes can push it over.

For any check that needs to talk to the MLflow server, run the command inside the **MLflow pod** instead — it has no such memory constraints.
