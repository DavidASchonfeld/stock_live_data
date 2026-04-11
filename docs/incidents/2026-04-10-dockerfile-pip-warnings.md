# Incident: Dockerfile pip Warnings — Cache Permission + Dependency Backtracking

**Date:** 2026-04-10
**Severity:** Non-blocking (warnings only, no deploy failure)

---

## Issue 1: pip Cache Permission Warning (Docker build step #10)

### Warning

```
WARNING: The directory '/tmp/.cache/pip' or its parent directory is not owned or
is not writable by the current user. The cache has been disabled. Check the
permissions and owner of that directory. If executing pip with sudo, you should
use sudo's -H flag.
```

Appeared in Docker build step `#10` during `RUN pip install "requests>=2.32.0"`.

---

### How It Was Encountered

Running `./scripts/deploy.sh`. The warning appeared in the Docker build output during the final Dockerfile layer — the `pip install "requests>=2.32.0"` step that runs as the `airflow` user.

---

### Root Cause

Docker build steps `#7` and `#8` run as `root` and pip writes its wheel cache to `/tmp/.cache/pip`, creating it root-owned. Step `#10` runs after `USER airflow`, so pip is now the `airflow` user — which cannot write to the root-owned `/tmp/.cache/pip`. pip detects this and emits the warning.

---

### How It Was Identified

The warning named the exact path (`/tmp/.cache/pip`). Cross-referencing the Dockerfile confirmed that `USER airflow` is set before step `#10` while the prior venv RUN steps (`#7`, `#8`) run as root — making the permission conflict structurally inevitable.

---

### Fix

Added `--no-cache-dir` to the final pip install in `airflow/docker/Dockerfile`:

```diff
- RUN pip install "requests>=2.32.0"
+ RUN pip install --no-cache-dir "requests>=2.32.0"
```

---

### Why This Fix

`--no-cache-dir` tells pip to skip all cache reads and writes. In a baked Docker image, the pip cache is never reused across builds (each `RUN` layer is isolated), so caching provides zero benefit here. Skipping it entirely is correct behavior, not a workaround. Alternatives — fixing `/tmp` permissions in the Dockerfile, setting `PIP_CACHE_DIR`, or reordering layers — would add complexity with no upside.

---

### How the Fix Solved the Problem

pip no longer attempts to access `/tmp/.cache/pip`. With no cache I/O, the ownership check never runs and the warning is never emitted. Package installation is unaffected.

---
---

## Issue 2: pip Dependency Backtracking Warning (Docker build step #7)

### Warning

```
INFO: pip is looking at multiple versions of dbt-adapters to determine which
version is compatible with other requirements. This could take a while.
INFO: This is taking longer than usual. You might need to provide the dependency
resolver with stricter constraints to reduce runtime. See
https://pip.pypa.io/warnings/backtracking for guidance.
```

Appeared in Docker build step `#7` during the dbt-venv `pip install` block.

---

### How It Was Encountered

Running `./scripts/deploy.sh`. The Docker build output showed pip iterating through 35+ candidate versions of `dbt-adapters` and ~30 candidate versions of `dbt-common` before settling. pip issued its backtracking warning after the resolution stalled, noticeably slowing step `#7`.

---

### Root Cause

`dbt-adapters` and `dbt-common` are transitive dependencies of `dbt-core==1.8.0` and `dbt-snowflake==1.8.0`, but were left unpinned in the Dockerfile. pip's dependency resolver had no starting point and had to trial-and-error its way through dozens of candidate versions to find a compatible set.

---

### How It Was Identified

pip's own guidance in the warning ("provide stricter constraints") pointed directly at the fix. Observing the final installed versions in the build output (`dbt-adapters-1.9.0`, `dbt-common-1.12.0`) confirmed that pinning those exact versions would let the resolver find the answer in a single lookup.

---

### Fix

Pinned both transitive dependencies explicitly in `airflow/docker/Dockerfile`:

```diff
  RUN python3 -m venv /opt/dbt-venv \
      && /opt/dbt-venv/bin/pip install --upgrade pip \
      && /opt/dbt-venv/bin/pip install \
           dbt-core==1.8.0 \
           dbt-snowflake==1.8.0 \
+          dbt-adapters==1.9.0 \
+          dbt-common==1.12.0 \
           openlineage-dbt \
      && chown -R airflow: /opt/dbt-venv
```

---

### Why This Fix

Pinning the exact versions that pip resolved on its own gives the resolver the answer directly — no iteration needed. The versions are identical to what pip chose without pinning, so there is no compatibility risk. Explicit pins also make the image reproducible: future rebuilds get the same versions regardless of what new releases appear on PyPI.

---

### How the Fix Solved the Problem

pip finds `dbt-adapters==1.9.0` and `dbt-common==1.12.0` immediately on first lookup. No backtracking occurs, no warning is emitted, and step `#7` completes faster.
