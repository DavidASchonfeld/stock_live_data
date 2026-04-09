# OpenLineage + dbt Fix Summary — 2026-04-08

Six changes total across two sessions. No Docker image rebuild required — `dbt-ol` was already installed by the existing `openlineage-dbt` pip install in the Dockerfile.
Deploy: `./scripts/deploy.sh`

---

## Changes and What Each Fixes

### 1. `airflow/dags/dbt/dbt_project.yml` — Removed `dispatch:` block

**Error fixed:** Spurious `[WARNING]: No packages found for openlineage_dbt` on every dbt run.

**Why:** The dispatch block told dbt to route macro calls through a package called `openlineage_dbt` before falling back to the built-in `dbt` namespace. That package does not exist on the dbt hub — `openlineage-dbt` is a pip package, not a dbt hub package, and it does not provide SQL macros via the dispatch mechanism. dbt silently fell back to its own macros every run and printed a warning. The dispatch approach is not how `openlineage-dbt` works.

---

### 2 & 3. `airflow/dags/dag_stocks.py` and `dag_weather.py` — `dbt run/test` → `dbt-ol run/test`

**Error fixed:** No `{"eventType": ...}` JSON events appearing in `dbt_run` or `dbt_test` task logs (T3_OPENLINEAGE_VERIFY.md Step 3 failing).

**Why:** Plain `dbt run` does not emit OpenLineage events. The `openlineage-dbt` pip package installs a wrapper binary called `dbt-ol` that internally calls `dbt run`, then reads `target/run_results.json` and `target/manifest.json` after the run completes and emits one START + COMPLETE event pair per model via the configured transport (`OPENLINEAGE_CONFIG=/opt/openlineage.yml`, console transport → stdout → visible in Airflow task logs). `--target-path /tmp/dbt_target` is added to both commands so `dbt-ol` knows where to find those artifact files.

---

### 4. `airflow/helm/values.yaml` — `AIRFLOW__OPENLINEAGE__EXECUTION_TIMEOUT: "60"`

**Error fixed:** `WARNING - OpenLineage process with pid X expired and will be terminated by listener` appearing in task logs, causing Airflow's built-in task-level OL START event to never emit.

**Why:** Airflow 3.x has its own built-in OpenLineage listener (separate from `dbt-ol`) that emits task-level START/COMPLETE/FAIL events for every Airflow task. It spawns a subprocess to emit the START event at task begin. The default timeout for that subprocess is 10 seconds. On a t3.large, importing the full Airflow provider stack takes ~11 seconds, so the subprocess was killed before it could emit. Setting the timeout to 60 seconds gives enough headroom. This preserves both layers of lineage: Airflow-level (task metadata) and dbt-level (model inputs/outputs from `dbt-ol`).

---

### 5. `airflow/dags/dbt/models/marts/fct_weather_hourly.sql` — Dedup partition changed to `observation_time` only (session 1)

---

### 6. `dag_stocks.py` and `dag_weather.py` — Removed `--target-path` CLI arg from all dbt-ol commands (session 2)

**Error fixed:** dbt-ol initializes correctly (logs show `OpenLineageClient will use 'console' transport`) and dbt runs successfully, but **zero model-level lineage events appear** in the log after dbt's "Completed successfully" line. The log ends there with no JSON events.

**Why:** The BashOperator commands passed both `DBT_TARGET_PATH=/tmp/dbt_target` (env var) and `--target-path /tmp/dbt_target` (CLI arg). dbt-ol 1.46.0 uses `DBT_TARGET_PATH` for its own artifact reading (post-run), but the duplicate `--target-path` CLI arg conflicts with dbt-ol's internal argument parsing — it passes the flag through to dbt but silently drops it from its own artifact path resolution, causing it to fall back to the default `{project_dir}/target/` location. Since no artifacts exist there (they're at `/tmp/dbt_target`), dbt-ol finds nothing to process and emits no events.

**Fix:** Removed `--target-path /tmp/dbt_target` from the CLI args in all four BashOperator bash_commands (dbt_run + dbt_test in both DAGs). Kept `DBT_TARGET_PATH=/tmp/dbt_target` as the env var — dbt reads it to know where to write artifacts, and dbt-ol reads it to know where to find them after the run. Added `mkdir -p /tmp/dbt_target /tmp/dbt_logs &&` prefix to defensively ensure the dirs exist before dbt-ol starts.

Also: the fork DeprecationWarning (`This process is multi-threaded, use of fork()`) was fixed in session 1 by adding `AIRFLOW__CORE__MP_START_METHOD: "spawn"` to `values.yaml`. This takes effect after `helm upgrade` (via `./scripts/deploy.sh`).

---

**Error fixed:** `dbt test --select tag:weather` exiting with code 2 (test failure on the `unique` test for `fct_weather_hourly.observation_time`).

**Why:** The old dedup partitioned by `(observation_time, latitude, longitude)`, keeping one row per timestamp-per-location. The `unique` test in `schema.yml` checks that `observation_time` is unique across the whole table. If Open-Meteo's grid snapping returned slightly different coordinates across API calls (e.g., `39.875` vs `40.0`), two rows with the same timestamp but different lat/lon would both survive the dedup (different partitions, both `rn = 1`). The `unique` test would then see duplicate `observation_time` values and fail. Since this is a single-location pipeline, `observation_time` is the natural primary key. Changing the partition to just `observation_time` keeps the single most-recently-imported row per timestamp regardless of coordinate drift, guaranteeing the `unique` test passes. The `materialized='table'` config means dbt fully rebuilds this table on the next run — no manual Snowflake cleanup needed.
