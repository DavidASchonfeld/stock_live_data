# Incident: `ModuleNotFoundError: No module named 'pkg_resources'` — ml-venv build failure

**Date:** 2026-04-10
**Severity:** Deploy blocked (no production impact)

---

## Error

```
ModuleNotFoundError: No module named 'pkg_resources'
```

Docker build aborted at Step 2b2 during the `/opt/ml-venv` RUN layer (step #8 of 5).

---

## Root Cause

`pkg_resources` is provided by `setuptools`. Python 3.12 does not bundle `setuptools` in virtual environments by default, so it must be installed explicitly.

The Dockerfile installed `setuptools` in a **separate pip step before** the main packages:

```dockerfile
&& /opt/ml-venv/bin/pip install setuptools \
&& /opt/ml-venv/bin/pip install mlflow==2.15.1 scikit-learn==1.5.2 ...
```

pip 26 (which the `--upgrade pip` step pulled in) resolves and installs ~80 packages in the second step. This process disturbs `setuptools`' registration in the venv's site-packages metadata, leaving `pkg_resources` unimportable even though `setuptools` itself was still present on disk.

---

## How It Was Encountered

`deploy.sh` runs a `docker build` and an `import pkg_resources` sanity check at the end of the ml-venv RUN layer. The check failed, aborting the build before the image was imported into K3S.

---

## How It Was Identified

The traceback pointed directly to the `import pkg_resources` line in the Dockerfile's RUN command. Reviewing the build log confirmed that `setuptools-82.0.1` was installed successfully in an earlier pip step, but was not present in the final installed-packages list — meaning the subsequent large `pip install` run had silently disrupted its registration without uninstalling it.

---

## Fix

Moved `setuptools` to install **after** all main packages:

```diff
- && /opt/ml-venv/bin/pip install setuptools \
  && /opt/ml-venv/bin/pip install \
       mlflow==2.15.1 \
       scikit-learn==1.5.2 \
       pandas==2.2.2 \
       snowflake-connector-python==3.10.1 \
+ && /opt/ml-venv/bin/pip install setuptools \
```

**File:** `airflow/docker/Dockerfile`, lines 22–34

---

## Why This Fix

Installing `setuptools` last guarantees that no subsequent pip operation can disturb its registration. The large `pip install` run resolves all dependencies first; `setuptools` is then written into the venv as the final step, with nothing left to overwrite it.

---

## How the Fix Solved the Problem

With `setuptools` installed last, its dist-info and `pkg_resources` entry point are written after all other packages have settled. The `import pkg_resources` check now runs against a clean, undisturbed setuptools installation and passes.
