# 2026-04-11 — Verification Step Fixes (Steps 3, 5, 7, 8, 9, 10)

After deploying the Thread 1 ML pipeline, six issues were found while running through `docs/verification-steps.md` on EC2.

---

## Issue 1 — Step 3: pip cache WARNING

**What happened:** Running `pip list` inside the scheduler pod printed a `WARNING: The directory '/tmp/.cache/pip' … is not owned or is not writable by the current user`.

**Why it happened:** The Dockerfile's `RUN python3 -m venv /opt/ml-venv` block runs as `root`, which causes pip to create `/tmp/.cache/pip` owned by root. Later, when the `airflow` user runs any pip command (including `pip list`), pip tries to write to that root-owned directory and fails.

**How it was identified:** Visible in the Step 3 `kubectl exec … pip list` output — appeared above the package listing.

**Fix:** Added `RUN mkdir -p /tmp/.cache/pip && chown -R airflow: /tmp/.cache/pip` to the Dockerfile just before the `USER airflow` switch. This gives the airflow user write access to the pip cache before it is needed, so pip can use the cache normally instead of printing the warning.

**File changed:** `airflow/docker/Dockerfile`

---

## Issue 2 — Step 5: DAG listed 5–10 times

**What happened:** `airflow dags list | grep stock_consumer` printed `stock_consumer_pipeline` seven times — identical rows.

**Why it happened:** Airflow 3.x runs multiple parallel DAG file processor workers. Each worker independently parses the DAG file and registers it in the database, producing one row per worker in `dags list`. This is expected Airflow 3.x behavior and does not cause the DAG to run more than once per trigger.

**How it was identified:** Noticed in Step 5 output when every row was an exact duplicate.

**Fix:** Documentation only — updated Step 5 in `verification-steps.md` to state that duplicates are expected and that the pass criterion is "appears at least once, no import errors."

---

## Issue 3 — Step 7: MLflow UserWarning about integer columns

**What happened:** Running `anomaly_detector.py` manually printed:
```
UserWarning: Hint: Inferred schema contains integer column(s)…
```

**Why it happened:** `IsolationForest.predict()` returns a `numpy.int64` array (`[1, -1]`). MLflow's `log_model` with `input_example` infers **both** the input and output schemas automatically. The existing `.astype("float64")` on the input example only fixed the input schema — MLflow still saw int64 in the model's output schema and fired the warning.

**How it was identified:** The warning appeared in Step 7 output even though `input_example` was already cast to float64.

**Fix:** Added `from mlflow.models import infer_signature` and built an explicit signature that casts `model.predict()` output to float before passing it to MLflow:
```python
sig = infer_signature(input_ex, model.predict(features_df).astype(float))
mlflow.sklearn.log_model(model, "isolation_forest", input_example=input_ex, signature=sig)
```
MLflow now sees a float output schema and does not fire the warning. The explicit signature also enables stronger input validation at inference time.

**File changed:** `airflow/dags/anomaly_detector.py`

---

## Issue 4 — Step 8: `list-runs` flags not recognized in Airflow 3.x

**What happened:** Two successive attempts at filtering by DAG ID both failed:
1. `airflow dags list-runs -d stock_consumer_pipeline --no-header` → `unrecognized arguments: -d --no-header`
2. `airflow dags list-runs --dag-id stock_consumer_pipeline` → `unrecognized arguments: --dag-id`

**Why it happened:** Airflow 3.x removed all flag-based DAG filtering from `dags list-runs`. The `dag_id` is now a **required positional argument**, not a flag.

**How it was identified:** Both commands returned non-zero exit codes and usage messages.

**Fix:** Updated Step 8 in `verification-steps.md` to use the correct Airflow 3.x positional syntax:
```bash
airflow dags list-runs stock_consumer_pipeline
```

---

## Issue 5 — Step 9: No guidance on where to find the mlflow_run_id

**What happened:** The verification step said to compare `MAX(mlflow_run_id)` to "the task log" but didn't explain where in the Airflow UI to look.

**How it was identified:** It was unclear during manual verification where the ID came from.

**Fix:** Updated Step 9 in `verification-steps.md` with step-by-step Airflow UI navigation: DAG → run → `detect_anomalies` task → Logs tab → last stdout line (the JSON dict). The `mlflow_run_id` field in that JSON is what must match Snowflake.

---

## Issue 6 — Step 10: MLflow experiment shows "no data" then crashes

**What happened:** After opening `http://localhost:5500`, the `anomaly_detection` experiment was visible but showed no runs. After a moment the page crashed with "Something went wrong."

**Why it happened (two parts):**
1. **"No data":** The experiment list page triggers a runs API call. This can fail silently if the `kubectl port-forward` process dropped during the long deploy — a known fragility documented in `2026-04-10-kubectl-port-forward-ssh-exit-255.md`.
2. **UI crash:** A known MLflow 2.15.1 React rendering bug where the experiment list page crashes when artifact metadata is partially unavailable or when the experiment was previously soft-deleted and restored.

**How it was identified:** The run WAS logged correctly (Step 7 printed the `🏃 View run` URL with a valid run ID and experiment ID 1). The issue was accessing the experiment via the list view rather than the direct run URL.

**Fix (two parts):**
1. **Documentation:** Updated Step 10 to navigate directly to the specific run URL (`http://localhost:5500/#/experiments/1/runs/<run_id>`) using the run ID from Step 9 — bypasses the crashing experiment list page entirely.
2. **Recovery function in `mlflow.sh`:** Added `step_restart_mlflow_pod()` which deletes the MLflow pod (forcing a clean restart) and re-establishes the port-forward. Centralizes the recovery in `deploy.sh` modules so it can be called without manual `kubectl` commands. Not called in the default deploy flow to avoid unnecessary pod churn.

**Files changed:** `docs/verification-steps.md`, `scripts/deploy/mlflow.sh`
