# T3 dbt 1.8 Deprecation Fixes

## Fix 1 — Moved `target-path` and `log-path` out of `dbt_project.yml`

**What:** dbt 1.8 dropped support for setting `target-path` and `log-path` inside `dbt_project.yml`. They still worked but printed a deprecation warning on every run.

**Why:** These two keys tell dbt where to write compiled SQL and logs. We needed them pointing to `/tmp` because the DAGs PVC is read-only and dbt would crash trying to write to its default location inside the project folder.

**Fix:** Removed the keys from `dbt_project.yml` and instead set `DBT_TARGET_PATH=/tmp/dbt_target` and `DBT_LOG_PATH=/tmp/dbt_logs` as environment variables in the dbt BashOperator in both DAGs. Same behavior, no warning.

---

## Fix 2 — Renamed `tests:` to `data_tests:` in `schema.yml`

**What:** dbt 1.8 renamed the `tests:` key (used to define column-level tests like `not_null` and `unique`) to `data_tests:`. Using the old name printed a deprecation warning on every run.

**Why:** The rename was done to disambiguate dbt's built-in data tests from generic pytest-style tests.

**Fix:** Replaced every `tests:` occurrence under `columns:` in `schema.yml` with `data_tests:`. No behavior change — same tests run, no warning.
