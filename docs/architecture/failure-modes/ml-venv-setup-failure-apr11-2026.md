# Incident: ml-venv Setup Failed After Full Deploy (Apr 11 2026)

## What Happened

During a full deploy, Step 7b printed the following warning and continued without aborting:

```
WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved.
Diagnose with: kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/pip list
```

The rest of the deploy completed normally (Step 7c onward). However, any Airflow run of the `detect_anomalies` task would fail immediately because `dag_stocks_consumer.py` calls `/opt/ml-venv/bin/python anomaly_detector.py` directly and that path was broken.

## Root Cause

Two problems combined to cause a silent failure:

**1. The fast-path check swallowed all errors with `2>/dev/null`.**
The check runs `import sklearn, mlflow, snowflake.connector, pandas` inside the container. If any import fails (most likely `snowflake.connector` — it has C extension dependencies that can fail at runtime even after a successful pip install), the error was completely hidden. The fallback branch then ran.

**2. The fallback's pip install used `--quiet`, hiding the real error.**
After attempting `python3 -m venv /opt/ml-venv` and `pip install --quiet ...`, the final per-package verification (a single 4-import check) failed for the same reason as the fast path. Because `--quiet` suppressed pip output, there was no indication of *which* package caused the failure.

The outer `|| { }` handler caught the non-zero exit and printed the WARNING without aborting the deploy.

**Why the Docker build didn't catch it:**
The Dockerfile verified only `importlib.metadata.version('setuptools')` — not the actual runtime imports. A broken `snowflake.connector` (e.g., a missing shared library) passes the Docker build but fails at deploy time.

## Impact

- The `detect_anomalies` Airflow task would fail on every run until fixed
- MLflow anomaly tracking was not recording results
- `PIPELINE_DB.ANALYTICS.FCT_ANOMALIES` table was not being updated

## Fix Applied

Three code changes were made:

**`scripts/deploy/airflow_pods.sh` — `step_setup_ml_venv()`:**
- Removed `2>/dev/null` from fast-path check so import errors appear in deploy logs
- Changed `python3 -m venv` to `python3 -m venv --upgrade` — idempotent, handles existing venv dir
- Removed `--quiet` from pip install so network and version errors surface
- Split the single 4-package verification into four individual `kubectl exec` calls — each prints which package passed or failed

**`airflow/docker/Dockerfile`:**
- Extended the build-time verification from just `setuptools` to all four runtime packages (`sklearn`, `mlflow`, `snowflake.connector`, `pandas`)
- Broken imports now fail the Docker build before the image is ever pushed, instead of failing silently at deploy time

**`scripts/deploy.sh`:**
- Added `--fix-ml-venv` flag: sources `common.sh` + `airflow_pods.sh`, calls `_wait_scheduler_exec` + `step_setup_ml_venv`, and exits
- Allows recovering a broken ml-venv in ~60 seconds without a full redeploy

## How to Recover if This Happens Again

Run the targeted fix without a full redeploy:

```bash
./scripts/deploy.sh --fix-ml-venv
```

This skips Docker build, Kafka, MLflow, pod restarts, and Helm — it only re-runs the ml-venv setup against the already-running scheduler pod.

If the fix script also fails, the verbose output (now that `2>/dev/null` and `--quiet` are removed) will show exactly which package import is failing. Common next steps:

- If `snowflake.connector` fails: check that `libssl` and `libffi` are available in the container (`apt list --installed`)
- If pip install fails with a network error: check EC2 outbound connectivity (`curl https://pypi.org`)
- If `python3 -m venv --upgrade` fails: check that the container has a writable `/opt/ml-venv` directory (`kubectl exec ... -- ls -la /opt/`)
