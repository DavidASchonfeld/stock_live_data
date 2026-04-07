# Failure Modes — Airflow (Scheduler + DAG Processor)

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### AF-1: DAG Configuration Drift

| Field | Detail |
|-------|--------|
| **Symptoms** | DAG appears in Airflow UI, then disappears after ~30 seconds. `airflow dags list` shows it intermittently. |
| **Root cause** | Dynamic `start_date` (e.g., `pendulum.now().subtract(days=1)`) evaluates to a different value on every parse cycle (~5s intervals). Airflow detects "configuration changed" and rejects the DAG as invalid. |
| **Blast radius** | Only the affected DAG. Other DAGs continue running. Downstream consumers (Flask dashboard) serve stale data. |
| **Prevention** | Always use fixed past dates: `pendulum.datetime(2025, 3, 29, tz="America/New_York")`. Never use `pendulum.now()` or `datetime.now()` in DAG-level config. |
| **Real incident?** | Yes — 2026-03-31. Stock DAG disappeared repeatedly. See [../../incidents/2026-03-31/](../../incidents/2026-03-31/). |

### AF-2: DAG Not Discovered (Module Variable Missing)

| Field | Detail |
|-------|--------|
| **Symptoms** | DAG file exists in `/opt/airflow/dags/`, passes syntax check, but doesn't appear in `airflow dags list`. No error messages in scheduler logs. |
| **Root cause** | The `@dag` decorator returns a DAG object when called. If the return value isn't assigned to a **module-level variable**, Airflow's DAG parser can't discover it. `stock_market_pipeline()` runs but the result is discarded. |
| **Blast radius** | Only the affected DAG. Silent failure — no errors anywhere. |
| **Prevention** | Always assign: `dag = stock_market_pipeline()`. Add to `deploy.sh` validation: check that DAG files contain a module-level assignment. |
| **Real incident?** | Yes — 2026-03-30. See [../../operations/TROUBLESHOOTING.md](../../operations/TROUBLESHOOTING.md). |

### AF-3: Init Container Blocks All Pods

| Field | Detail |
|-------|--------|
| **Symptoms** | All Airflow pods (scheduler, triggerer, api-server) stuck at `Init:0/1` or `Init:CrashLoopBackOff` indefinitely. |
| **Root cause** | Every Airflow pod runs a `wait-for-airflow-migrations` init container that blocks until the DB is reachable and fully migrated. Two known triggers: (1) PostgreSQL pod is down (e.g., `ImagePullBackOff`); (2) the migration job itself never ran because all pods (including the migration job pod) had `CreateContainerConfigError` from a missing secret — see AF-7. |
| **Blast radius** | **Total Airflow outage.** All DAGs stop. No new data ingested. Flask serves stale data. |
| **Prevention** | Pin PostgreSQL image tags. After a major Helm chart upgrade, verify the migration job pod started and completed before diagnosing init container crashes. |
| **Real incident?** | Yes — 2026-03-30 (PostgreSQL image). Yes — 2026-04-06 (missing secret blocked migration job). See CHANGELOG.md. |

### AF-4: XCom Serialization Mismatch

| Field | Detail |
|-------|--------|
| **Symptoms** | Transform task succeeds, load task fails or inserts wrong data. DataFrame columns in load task don't match what transform produced. |
| **Root cause** | XCom serializes task outputs to JSON and deserializes on the receiving end. `DataFrame.to_dict(orient="records")` produces a list of dicts, but if the DataFrame has unexpected structure (e.g., nested objects), the round-trip through JSON changes the shape. |
| **Blast radius** | Data corruption — wrong columns or values inserted into MariaDB. Flask serves garbage data. Silent unless schema validation is in place. |
| **Prevention** | Validate DataFrame schema (column names + types) in both transform output and load input. Assert expected columns before `to_sql()`. |
| **Real incident?** | Yes — Weather DAG `load()` task received wrong column structure. |

### AF-5: DAG Processor Filesystem Cache Stale

| Field | Detail |
|-------|--------|
| **Symptoms** | DAG visible after deploy, disappears after ~90 seconds. `airflow dags reserialize` brings it back temporarily. Scheduler logs show successful parse. |
| **Root cause** | Scheduler and Processor pods both mount the same `hostPath` volume, but each gets its own Linux filesystem cache. After `rsync` updates files on EC2, the Scheduler sees the new inode but the Processor pod retains a cached old directory listing. Airflow's sync cycle queries the Processor's stale view, can't find the file, marks DAG `is_stale: True`. |
| **Blast radius** | Only newly deployed DAGs. Existing DAGs unaffected. |
| **Prevention** | After deploying files, restart both Scheduler AND Processor pods. Or migrate DAGs to ConfigMap-based deployment. |
| **Real incident?** | Yes — 2026-03-31. Stock DAG 90-second staleness cycle. |

### AF-6: Scheduler OOMKilled After Major Version Upgrade

| Field | Detail |
|-------|--------|
| **Symptoms** | Scheduler starts, runs for 2-4 minutes, then `OOMKilled` (exit code 137). Repeats in a loop. |
| **Root cause** | Airflow 3.x uses a supervisor model that spawns ~15 worker subprocesses at startup, each loading the full provider stack. Memory spikes well above the 2.x-era 1 Gi limit, which was sized for the old single-process scheduler. |
| **Blast radius** | Scheduler down, DAGs not triggered. Triggerer and api-server unaffected. |
| **Prevention** | After any Airflow major version upgrade, review memory limits. Airflow 3.x scheduler needs 2 Gi limit on a t3.large. |
| **Real incident?** | Yes — 2026-04-06. See CHANGELOG.md. |

### AF-7: All Pods `CreateContainerConfigError` — Missing Chart Secret After Major Upgrade

| Field | Detail |
|-------|--------|
| **Symptoms** | Every pod in the namespace (scheduler, api-server, dag-processor, triggerer, AND the migration job) gets `CreateContainerConfigError`. Nothing starts. `kubectl describe pod` shows: `Error: secret "airflow-webserver-secret-key" not found`. |
| **Root cause** | `enableBuiltInSecretEnvVars.AIRFLOW__WEBSERVER__SECRET_KEY` defaults to `true`. In Airflow 3.x, the chart no longer creates `airflow-webserver-secret-key` (that secret is 2.x-only — replaced by `airflow-api-secret-key`). Every pod spec references the nonexistent secret, so no pod can start. Because the migration job also can't start, the DB is never migrated, and all init containers wait forever. |
| **Blast radius** | **Total cluster outage.** Cascade: missing secret, migration job fails, DB not migrated, all init containers crash, all pods down. |
| **Prevention** | When upgrading from Airflow 2.x to 3.x: add `enableBuiltInSecretEnvVars.AIRFLOW__WEBSERVER__SECRET_KEY: false` to `values.yaml` before running `helm upgrade`. |
| **Real incident?** | Yes — 2026-04-06. Root cause of 4 consecutive failed `helm upgrade` attempts spanning several hours. See CHANGELOG.md. |

### AF-8: Helm Upgrade Without Version Pin — Accidental Major Version Jump

| Field | Detail |
|-------|--------|
| **Symptoms** | `helm upgrade` succeeds initially, migration job runs, then upgrade times out. Subsequent rollback fails with DB schema mismatch error. Cluster stuck between versions. |
| **Root cause** | Running `helm upgrade` without `--version <tag>` pulls the latest chart. If a major version has been released, the migration job upgrades the DB schema before the timeout occurs. After timeout, the DB is at the new schema but pods may still be on the old version — and Airflow cannot downgrade its DB. |
| **Blast radius** | Forced migration to the new major version. All subsequent `helm upgrade` attempts with the old chart version fail. |
| **Prevention** | Always use `--version` in `helm upgrade` commands. Pin in `scripts/deploy.sh`. |
| **Real incident?** | Yes — 2026-04-05 (rev 18). Caused accidental 2.9.3 to 3.1.8 upgrade. See CHANGELOG.md. |
