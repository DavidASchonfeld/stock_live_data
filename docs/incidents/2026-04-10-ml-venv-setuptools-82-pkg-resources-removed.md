# Incident: `pkg_resources` Missing Despite `setuptools` Installed — setuptools 82 Removal

**Date:** 2026-04-10
**Severity:** Deploy blocked (no production impact)

---

## Error

```
ModuleNotFoundError: No module named 'pkg_resources'
```

Docker build aborted at Step 2b2 during the `/opt/ml-venv` RUN layer.

---

## How It Was Encountered

Running `./scripts/deploy.sh`. The build installed all ML packages and then `setuptools-82.0.1` successfully — yet the post-install sanity check `import pkg_resources` failed immediately after.

---

## Root Cause

`setuptools 80+` completed the long-running deprecation of `pkg_resources` in favor of `importlib.metadata`. Starting in setuptools 80, the `pkg_resources` module directory is no longer written to site-packages. `pip install setuptools` with pip 26 resolves to the latest version (82.0.1), which has no `pkg_resources` to install.

Prior incident fixes that moved setuptools to a separate last-install step did not resolve this because the module is absent entirely — not just mis-registered.

---

## How It Was Identified

The build log showed `Successfully installed setuptools-82.0.1` immediately followed by `ModuleNotFoundError: No module named 'pkg_resources'`. Since the module was outright missing (not present but unimportable), the root cause was that setuptools 82 simply doesn't ship it anymore.

---

## Fix

Pinned setuptools below the version that removed `pkg_resources` in `airflow/docker/Dockerfile`:

```diff
- && /opt/ml-venv/bin/pip install setuptools \
+ && /opt/ml-venv/bin/pip install "setuptools<75" \
```

---

## Why This Fix

setuptools 67–74.x ships `pkg_resources` as a stable, importable module. setuptools 75+ began the removal process; 80+ completed it. Pinning `<75` is the most conservative bound that guarantees `pkg_resources` is present without restricting unnecessarily. mlflow 2.15.1 hard-imports `pkg_resources` in `mlflow/utils/requirements_utils.py` and cannot be patched around it.

---

## How the Fix Solved the Problem

With `setuptools<75`, pip resolves to a version in the 74.x range and writes the `pkg_resources/` directory into the venv's site-packages. The `import pkg_resources` check passes, the Docker build completes, and the image is imported into K3S.
