# Incident: ml-venv Fast-Path OOM-Kill (Apr 11, 2026)

## What Happened

Running `./scripts/deploy.sh --fix-ml-venv` failed with exit code 137 on the first `kubectl exec`, then a second exec failed entirely with:

```
error executing command in container: failed to start exec ...: OCI runtime exec failed:
unable to start container process: signal: killed (possibly OOM-killed);
failed to open /proc/<PID>/ns/ipc: No such file or directory
```

The anomaly detector could not run.

## Root Cause

The fast-path health check in `step_setup_ml_venv()` verified the venv by importing all four ML packages in one Python process:

```bash
kubectl exec ... -- /opt/ml-venv/bin/python -c 'import sklearn, mlflow, snowflake.connector, pandas'
```

Importing mlflow + scikit-learn + snowflake-connector-python + pandas simultaneously loads 500–800 MB of package code into memory. On top of the already-running Airflow scheduler (which uses most of the pod's 2 Gi limit), this pushed the exec'd process over the memory limit and OOM-killed it (exit 137).

Because exit 137 looked like a broken venv, the script fell into the rebuild fallback and attempted a `pip install` of all 7 packages in one command. That resolver run used even more memory and OOM-killed the scheduler container entirely — making the container's `/proc` namespace unreachable for the retry exec.

**Key point:** The venv was correctly baked into the Docker image the whole time. This was a false failure — only the verification method was wrong.

## Fix

**`scripts/deploy/airflow_pods.sh` — `step_setup_ml_venv()`**

1. **Fast-path changed from `python -c 'import ...'` to `pip show`**
   `pip show` reads package metadata from disk (no Python runtime, no imports). It exits 0 if all packages are present, exits 1 if any is missing. Zero memory spike.

2. **Fallback pip install split into one package at a time**
   Instead of resolving all 7 packages in one `pip install` command, each package is installed in a separate exec. This lets each resolver run complete and release memory before the next begins, reducing peak memory usage in the fallback path.

3. **Warning message updated**
   Now explicitly states that if the fallback pip install keeps OOM-killing the container, a full redeploy (Docker image rebuild) is needed rather than retrying `--fix-ml-venv` in a loop.

## How to Verify

Run `./scripts/deploy.sh --fix-ml-venv`. Expected output:

```
ml-venv package check passed (pip show) — skipping reinstall
ml-venv ready at /opt/ml-venv
```

No exit 137. No fallback triggered. Then confirm the `detect_anomalies` task succeeds on the next DAG run.

## Lessons

- Never verify a venv by importing heavy packages inside a memory-constrained pod — use `pip show` or check the binary path instead.
- A fast-path that can OOM-kill is worse than no fast-path: it triggers an even more memory-intensive fallback.
- The Docker image baking the venv is the reliable path. The runtime fallback exists for emergencies only; if it fails repeatedly, rebuild the image.
