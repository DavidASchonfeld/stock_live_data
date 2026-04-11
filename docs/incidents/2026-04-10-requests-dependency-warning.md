# Incident: `RequestsDependencyWarning` — urllib3/chardet Version Mismatch

**Date:** 2026-04-10
**Severity:** Non-blocking (warning only, no runtime failure)

---

## Warning

```
/home/airflow/.local/lib/python3.12/site-packages/requests/__init__.py:113
RequestsDependencyWarning: urllib3 (2.6.3) or chardet (6.0.0.post1)/charset_normalizer (3.4.5)
doesn't match a supported version!
```

Appeared on every `airflow` CLI invocation inside the scheduler pod (e.g. `airflow dags list`, `airflow tasks list`).

---

## How It Was Encountered

Running the post-deploy verification steps against the live scheduler pod:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list | grep stock_consumer
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow tasks list stock_consumer_pipeline
```

Both commands produced the warning as the first line of stderr before any useful output.

---

## Root Cause

The `requests` package (an older version shipped with `apache/airflow:3.1.8`) contains a startup check in `requests/__init__.py` that validates the installed versions of its dependencies — `urllib3`, `chardet`, and `charset_normalizer`. If those versions fall outside the range the `requests` version was tested against, it emits a `RequestsDependencyWarning`.

In this environment, transitive dependencies pulled in by `snowflake-connector-python` and other packages upgraded `urllib3` to `2.6.3` and `chardet` to `6.0.0.post1` — both newer than what the installed `requests` version expected. The check fires on every Python process that imports `requests`, which includes the Airflow CLI.

This warning check was **removed entirely** in `requests 2.32.0` (released May 2024), as the library matured to support the newer dependency ranges without explicit version-gating.

---

## How It Was Identified

The warning path pointed directly at `requests/__init__.py:113` — the well-known version compatibility check. Cross-referencing the `requests` changelog confirmed the check was dropped in `2.32.0`, making the fix obvious: upgrade `requests` past that version.

---

## Fix

Two places were updated to ensure consistent suppression across both the baked image and the dynamically rebuilt ml-venv:

**`airflow/docker/Dockerfile`** — upgrade `requests` in Airflow's base Python after switching to the `airflow` user:

```dockerfile
USER airflow
# Upgrade requests ≥2.32.0 — removes urllib3/chardet version-mismatch warning (warning was removed in requests 2.32.0)
RUN pip install "requests>=2.32.0"
```

**`scripts/deploy.sh`** — Step 7b adds `"requests>=2.32.0"` to the dynamic ml-venv pip install:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/pip install --quiet --no-cache-dir \
        ...
        "requests>=2.32.0" &&
```

---

## Why This Fix

Upgrading `requests` is the correct fix rather than suppressing the warning with `warnings.filterwarnings()`. The warning exists because an old version of `requests` genuinely doesn't know whether newer urllib3/chardet builds are safe — the right answer is to use a version of `requests` that does. `requests>=2.32.0` was released well before `apache/airflow:3.1.8`, so there is no compatibility risk.

---

## How the Fix Solved the Problem

`requests 2.32.0` removed the version-compatibility check from `__init__.py` entirely. Once upgraded, importing `requests` no longer inspects urllib3/chardet versions at all, so no warning is emitted regardless of what versions are installed alongside it.
