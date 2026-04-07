# Changelog — What Was Fixed

This file contains the most recent changes. For older entries, see [_archive/CHANGELOG_ARCHIVE.md](_archive/CHANGELOG_ARCHIVE.md).

---

## 2026-04-07: Airflow ImagePullBackOff — Pod startup failure after Helm 3.x upgrade

**Problem:** After upgrading the Airflow Helm chart, all Airflow pods entered `ImagePullBackOff` state and could not start. The Airflow UI was completely unreachable, while the Flask dashboard continued working normally.

**Root causes:** Three interconnected issues:
1. **Obsolete Docker images** — The Helm chart was trying to pull Bitnami-maintained images that no longer exist on Docker Hub
2. **Invalid Kubernetes YAML** — The `scheduler/livenessProbe` in `values.yaml` had incorrect syntax (`command:` instead of `exec:`)
3. **Resource caching** — Existing StatefulSet and Deployment objects retained old pod templates; Helm patches were insufficient to force recreation

**Fix:** Added explicit image overrides for Apache and Redis images, corrected probe YAML syntax, added `--force` and `--wait` flags to deploy.sh Helm upgrade.

**Files changed:** `airflow/helm/values.yaml`, `scripts/deploy.sh`

---

## 2026-04-06: Pre-Snowflake Health Check — dag-processor and triggerer probe timeouts fixed

**Problem:** `airflow-dag-processor` was in CrashLoopBackOff (41 restarts) and `airflow-triggerer-0` was failing its liveness probe every ~5 minutes. Both showed exit code 0 / signal 15 — not real crashes, but Kubernetes killing them because the liveness probe timed out.

**Root cause:** Liveness probe timeout was 20s; the probe command takes 30-45s on this cluster (same issue previously fixed for scheduler and api-server).

**Fix:** Added `livenessProbe.timeoutSeconds: 45` to both components in values.yaml. Added triggerer restart to deploy.sh.

**Files changed:** `airflow/helm/values.yaml`, `scripts/deploy.sh`

---

## 2026-04-06: Code Cleanup Pass — Naming, Dead Code, Deploy Reliability

A systematic cleanup addressing naming conventions, dead code, and deploy reliability issues accumulated during Step 1.

**Key changes:** Renamed camelCase functions/variables to snake_case across DAGs and clients. Deleted dead 3-line re-export wrapper (`stock_client.py`). Renamed `file_logger.print()` → `.log()` to stop shadowing Python built-in. Fixed deploy.sh syntax check (was silently passing broken files). Replaced `sleep 60` with `kubectl wait`. Commented out Step 2 Snowflake deps in requirements.txt.

**Files changed:** `weather_client.py`, `dag_weather.py`, `dag_stocks.py`, `file_logger.py`, `alerting.py`, `validate_database.py`, `deploy.sh`, `requirements.txt`, `pod-flask.yaml`
