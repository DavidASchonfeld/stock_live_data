# Incident: Docker build fails — `pkg_resources` missing despite `setuptools` installed

**Date:** 2026-04-10
**Component:** Airflow Docker image build — `/opt/ml-venv`

---

## Error

```
ModuleNotFoundError: No module named 'pkg_resources'
```

Build step #8 (ml-venv creation) aborted:

```
#8 90.09   File "<string>", line 1, in <module>
#8 90.09 ModuleNotFoundError: No module named 'pkg_resources'
#8 ERROR: process "/bin/bash ... python3 -m venv /opt/ml-venv ... pip install setuptools mlflow==2.15.1 ..." did not complete successfully: exit code: 1
```

---

## How It Was Encountered

Running `./scripts/deploy.sh`. The build reached the ml-venv step, installed all packages successfully (including `setuptools-82.0.1`), then failed at the post-install verification:

```dockerfile
&& /opt/ml-venv/bin/python -c "import pkg_resources"
```

Despite `setuptools` appearing in pip's "Successfully installed" output, the verification import failed immediately after.

---

## Root Cause

On **Python 3.12 + pip 26**, installing `setuptools` as part of a larger `pip install` batch leaves `pkg_resources` in an inconsistent state. When other packages install after `setuptools` in the same invocation, pip 26's post-install processing disturbs setuptools' site-packages registration — overwriting or re-ordering metadata in a way that makes `pkg_resources` unfindable by the time the invocation completes.

---

## Fix

Move `setuptools` into its own `pip install` step **after** the main batch so nothing runs after it.

**File:** `airflow/docker/Dockerfile`

```diff
  RUN python3 -m venv /opt/ml-venv \
      && /opt/ml-venv/bin/pip install --upgrade pip \
      && /opt/ml-venv/bin/pip install \
-          setuptools \
           mlflow==2.15.1 \
           scikit-learn==1.5.2 \
           pandas==2.2.2 \
           snowflake-connector-python==3.10.1 \
+     && /opt/ml-venv/bin/pip install setuptools \
      && /opt/ml-venv/bin/python -c "import pkg_resources"
```

---

## Why This Fix

Installing `setuptools` last means no subsequent pip activity can disturb its registration. Each pip invocation is atomic — it fully commits all dist-info metadata before returning. With `setuptools` as the final install, `pkg_resources` is cleanly registered when the verification import runs immediately after.

---

## How the Fix Solved the Problem

The verification step `import pkg_resources` runs right after the `setuptools`-only pip invocation. Since nothing else installs after it, pip 26 has no opportunity to interfere with its metadata. The import succeeds and the Docker build completes.
