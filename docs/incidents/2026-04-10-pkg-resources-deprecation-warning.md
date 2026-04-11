# Incident: `pkg_resources` DeprecationWarning in Docker Build

**Date:** 2026-04-10
**Severity:** Non-blocking (warning only, no deploy failure)

---

## Warning

```
<string>:1: DeprecationWarning: pkg_resources is deprecated as an API.
See https://setuptools.pypa.io/en/latest/pkg_resources.html
```

Appeared in Docker build step `#8` during the `/opt/ml-venv` RUN layer.

---

## How It Was Encountered

Running `./scripts/deploy.sh`. All packages installed successfully, but the post-install sanity check — `python -c "import pkg_resources"` — emitted a `DeprecationWarning` in the build log immediately after `setuptools-74.1.3` was installed.

---

## Root Cause

`pkg_resources` is a legacy setuptools API that has been deprecated in favor of the stdlib `importlib.metadata` module (available since Python 3.8). Even in setuptools `<75` (where the module still ships), importing it triggers the warning because setuptools explicitly marks the API as deprecated. The Dockerfile sanity check was calling the deprecated API directly.

---

## How It Was Identified

The build output showed the warning on the `python -c "import pkg_resources"` line — the only place in the build that explicitly imports it. Cross-referencing with the setuptools changelog confirmed `pkg_resources` is deprecated API and the warning is intentional.

---

## Fix

Replaced the sanity check in `airflow/docker/Dockerfile` with the modern `importlib.metadata` equivalent:

```diff
- && /opt/ml-venv/bin/python -c "import pkg_resources" \
+ && /opt/ml-venv/bin/python -c "import importlib.metadata; importlib.metadata.version('setuptools')" \
```

---

## Why This Fix

`importlib.metadata` is the official stdlib replacement for `pkg_resources` and produces no deprecation warnings. Calling `.version('setuptools')` preserves the original intent — confirming that setuptools is installed and importable — without touching the deprecated API. Suppressing the warning was intentionally avoided, as the right fix is to not call deprecated APIs.

---

## How the Fix Solved the Problem

The sanity check no longer imports `pkg_resources` at all. `importlib.metadata` is part of the Python standard library and needs no separate install. The build step now completes cleanly with no warnings.
