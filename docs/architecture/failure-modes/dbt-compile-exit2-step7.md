# dbt compile exit code 2 — RESTORE_VERIFICATION Step 7

**Date:** 2026-04-12
**Component:** dbt / Restore Verification
**Severity:** Medium — blocks restore verification but not the running pipeline

---

## Symptom

Running the RESTORE_VERIFICATION.md Step 7 compile check:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "DBT_PROFILES_DIR=/dbt /opt/dbt-venv/bin/dbt compile \
    --project-dir /opt/airflow/dags/dbt --select tag:stocks"
```

Returns `command terminated with exit code 2` with **zero terminal output** — no error message, no stack trace, nothing from dbt.

---

## Root Cause

Two compounding issues:

**1. dbt 1.8.x writes all CLI output to stderr, not stdout.**
`kubectl exec` without a TTY (`-t`) separates stdout and stderr — stderr is silently discarded. `dbt --version`, compile output, and error messages all go to stderr and never reach the terminal. Fix: wrap every dbt command in `bash -c "... 2>&1"` to merge the streams.

**2. Missing `--debug` flag — dbt 1.8.x silently routes output to a log file.**
When `DBT_LOG_PATH` is set, dbt 1.8.x writes all output (including the final success/error line) to `$DBT_LOG_PATH/dbt.log` and prints nothing to the terminal. Without `--debug`, the terminal is completely silent regardless of whether the command succeeds or fails. This made the exit code impossible to interpret.

**2. Verification command didn't match DAG invocation.**
The DAG tasks in `dag_stocks_consumer.py` always set three env vars before calling dbt:
- `DBT_PROFILES_DIR=/dbt` — tells dbt where profiles.yml is mounted
- `DBT_TARGET_PATH=/tmp/dbt_target` — writes compiled output to a tmp dir
- `DBT_LOG_PATH=/tmp/dbt_logs` — writes logs to a tmp dir

The original verification command only set `DBT_PROFILES_DIR` and omitted the other two, causing dbt to try writing to the project directory itself and failing silently.

**Confirmed working:** With `--debug` added and all three env vars set, `dbt compile --select tag:stocks` exits 0 and prints `Command 'dbt compile' succeeded`. All 5 models and 18 tests compiled cleanly against Snowflake in ~10 seconds.

---

## Fix

Updated RESTORE_VERIFICATION.md Step 7 with:

1. A pre-check that confirms `/dbt/profiles.yml` is mounted before attempting compile.
2. The corrected compile command that sets `DBT_TARGET_PATH`, `DBT_LOG_PATH`, pre-creates those tmp dirs with `mkdir -p`, and adds `--debug` to force output to the terminal.

The corrected command:
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    bash -c "mkdir -p /tmp/dbt_target /tmp/dbt_logs && \
    DBT_PROFILES_DIR=/dbt \
    DBT_TARGET_PATH=/tmp/dbt_target \
    DBT_LOG_PATH=/tmp/dbt_logs \
    /opt/dbt-venv/bin/dbt --debug compile \
    --project-dir /opt/airflow/dags/dbt \
    --select tag:stocks \
    --no-use-colors"
```

---

## How to Diagnose If It Still Fails

If exit code 2 persists after the fix:

```bash
# Read the dbt log to see the actual error
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- cat /tmp/dbt_logs/dbt.log
```

- **"Could not find profile named 'pipeline_dbt'"** → `dbt-profiles` secret not mounted. Re-run `./scripts/deploy.sh`.
- **"Env var required but not provided: SNOWFLAKE_ACCOUNT"** → `snowflake-credentials` secret missing or pod not restarted. Re-run deploy.
- **Snowflake auth/connection error** → Credentials are wrong. Check `kubectl get secret snowflake-credentials -n airflow-my-namespace -o yaml` and compare against `.env.deploy.example`.

---

## Prevention

Two rules for any `kubectl exec` command that calls dbt:
1. Always wrap in `bash -c "... 2>&1"` — dbt writes to stderr, which kubectl discards without a TTY.
2. Always include `--debug` for verification commands — dbt 1.8.x routes compile/run output to a log file by default when `DBT_LOG_PATH` is set.
