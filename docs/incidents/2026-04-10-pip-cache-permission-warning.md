# Incident: pip Cache Permission Warning During ml-venv Setup

**Date:** 2026-04-10
**Severity:** Non-blocking (warning only, no deploy failure)

---

## Warning

```
WARNING: The directory '/tmp/.cache/pip' or its parent directory is not owned or
is not writable by the current user. The cache has been disabled. Check the
permissions and owner of that directory. If executing pip with sudo, you should
use sudo's -H flag.
```

Appeared during `deploy.sh` Step 7b when installing packages into `ml-venv` inside the Airflow scheduler pod.

---

## How It Was Encountered

Running `./scripts/deploy.sh`. Step 7b executed `kubectl exec` into `airflow-scheduler-0` and ran `pip install` inside the pod. The warning appeared immediately before package installation and was visible in the deploy output.

---

## Root Cause

pip defaults its cache directory to `$XDG_CACHE_HOME/pip` (typically `~/.cache/pip`). Inside the Airflow scheduler container, the home directory resolves such that pip falls back to `/tmp/.cache/pip`. The `/tmp` directory inside the container is owned by a different user than the one pip is running as, making the cache path unwritable. pip detects this and emits the warning.

---

## How It Was Identified

The warning message named the exact path (`/tmp/.cache/pip`) and stated it was not writable. Cross-referencing with the deploy script confirmed pip was invoked via `kubectl exec` — no `-H` flag context, no explicit cache dir set — leaving pip to resolve a container-internal path it couldn't write to.

---

## Fix

Added `--no-cache-dir` to the `pip install` invocation in `scripts/deploy.sh` Step 7b:

```diff
- /opt/ml-venv/bin/pip install --quiet --upgrade \
+ /opt/ml-venv/bin/pip install --quiet --no-cache-dir --upgrade \
```

---

## Why This Fix

`--no-cache-dir` tells pip to skip the cache entirely — no read, no write. Since `ml-venv` is torn down and rebuilt from scratch on every deploy (the scheduler pod is restarted in Step 7 immediately before Step 7b), the pip cache provides zero benefit: packages are always freshly installed regardless. Skipping the cache is therefore the correct behavior, not just a workaround.

Alternative approaches (fixing `/tmp` permissions, setting `PIP_CACHE_DIR`, passing `-H` via sudo) were rejected as unnecessary complexity for a cache that was never useful here.

---

## How the Fix Solved the Problem

pip no longer attempts to read from or write to `/tmp/.cache/pip`. With no cache interaction, the permission check never runs and the warning is never emitted. Package installation is unaffected — pip simply fetches packages from PyPI directly each time, which it was already effectively doing since the cache was disabled anyway.
