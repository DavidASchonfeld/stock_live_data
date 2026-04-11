# Incident: deploy.sh Runtime Reduced from ~22 min to ~7-10 min

**Date:** 2026-04-10
**Severity:** Low (developer productivity / iteration speed)
**Affected component:** `scripts/deploy.sh`

---

## What caused the slow deploys

Three root causes combined to make every deploy take ~22 minutes:

**1. All steps ran sequentially even when independent.**
Kafka (~7-10 min) and MLflow (~3-5 min) had no dependency on each other, but the script waited for Kafka to fully roll out before even starting MLflow. Similarly, three `kubectl wait` commands for Airflow pod readiness (each up to 360s) ran one after another — up to 18 min total — even though all three pods had already been deleted before the first wait began.

**2. Docker build cache was disabled unnecessarily.**
`docker build --no-cache` (line 207) forced Docker to rebuild every Dockerfile layer from scratch on every deploy, even when nothing in the Dockerfile or its inputs changed. This added 2-5 min to every deploy. The comment in the script claimed "Docker layer cache makes this fast on repeat deploys" — directly contradicting the `--no-cache` flag. This was a bug: `--no-cache` was added to fix a K3S containerd stale-snapshot problem, but that problem was already solved separately by the BUILD_TAG timestamp + `k3s ctr images rm` steps.

**3. No fast path for the common "fix a DAG and redeploy" workflow.**
Even changing a single line in a DAG file triggered a full Docker image rebuild, Kafka redeploy, MLflow redeploy, Flask rebuild, and Helm upgrade — none of which needed to change.

---

## How it was identified

Deploy timing was observed directly: each full deploy was taking ~22 minutes. The main bottlenecks were identified by tracing the sequential dependency graph in `deploy.sh`:
- Kafka rollout wait (`kubectl rollout status --timeout=480s`) was a single blocking call
- MLflow rollout wait (`kubectl rollout status --timeout=180s`) ran only after Kafka finished
- Three `kubectl wait` calls for Airflow pods ran sequentially in one SSH string
- `docker build --no-cache` rebuilt the full image layer stack every time

The `--no-cache` bug was found by comparing the comment on lines 196-199 ("Docker layer cache makes this fast") against the actual command on line 207 (`--no-cache`).

---

## What was changed and why

### 1. Removed `--no-cache` from Airflow Docker build

**Why:** `--no-cache` was disabling Docker's layer cache, which is the primary mechanism for making repeat builds fast. The K3S cache problem `--no-cache` was trying to solve is handled correctly by two existing mechanisms: (a) the BUILD_TAG timestamp ensures K3S always sees a new image tag, and (b) `k3s ctr images rm` purges stale snapshots before import. Removing `--no-cache` allows Docker to reuse unchanged layers (e.g., the pip install layer) when only DAG files changed — cutting build time from 2-5 min to 10-30s for the common case.

**Why it's safe:** The Dockerfile has no `COPY` of DAG files (DAGs are rsynced to a volume separately). So changing a DAG does not affect any Docker layer, and Docker's cache reuse is correct. If the Dockerfile itself changes (e.g., new package version), Docker detects the change and rebuilds from that layer forward.

### 2. Added `--dags-only` flag

**Why:** The most common development action is changing a Python file in `airflow/dags/`. In this case, Docker images, Kafka, MLflow, Flask, and Helm are all unchanged. Skipping them cuts deploy time from ~22 min to ~5-7 min.

**How to use:**
```bash
./scripts/deploy.sh --dags-only   # only changed .py files in airflow/dags/
./scripts/deploy.sh               # full deploy (Dockerfile, values.yaml, Kafka, MLflow, Flask, etc.)
```

### 3. Parallelized Kafka + MLflow deployments

**Why:** These two deployments are fully independent — Kafka does not depend on MLflow and vice versa. Running them sequentially left CPU and network idle for the duration of the slower job. Running them as background bash jobs (with proper exit-code checking via `_wait_bg`) allows them to run simultaneously.

**How:** Both are launched with `&` immediately after file syncs complete. Their PIDs are checked before the Helm upgrade, which requires both to be running (Airflow DAGs connect to Kafka and MLflow at startup).

### 4. Parallelized Airflow pod readiness waits

**Why:** All three Airflow pods (scheduler, dag-processor, triggerer) are deleted before any wait begins. Each `kubectl wait` is independent. Running them sequentially meant the triggerer didn't even start its wait until the scheduler AND dag-processor were both Ready — adding up to 12 min of unnecessary serial waiting.

**How:** Each `kubectl wait` is launched as a background SSH call (`ssh ... &`). All three PIDs are checked via `_wait_bg`. Maximum wall time drops from 3×360s to 360s.

### 5. Added `_wait_bg` helper

**Why:** bash's `set -e` does not propagate failures from background jobs. Without explicit exit-code checking, a failed background SSH job would silently disappear and the script would continue as if the deploy succeeded. `_wait_bg` closes this gap by calling `wait $PID` and exiting with an error message if the job failed.

### 6. Split deploy.sh into modules

**Why:** `deploy.sh` was >800 lines / >14K tokens, making it difficult to scan, navigate, and edit. Splitting into sourced module files keeps each file focused and scannable. `deploy.sh` is now a thin orchestrator that calls functions defined in `scripts/deploy/`.

**New structure:**
```
scripts/
  deploy.sh              # orchestrator: sources modules, parses args, controls flow
  deploy/
    common.sh            # shared vars, _wait_bg, _print_deploy_summary, .env.deploy loading
    setup.sh             # EC2 dir setup, kubectl chmod, Python syntax validation
    sync.sh              # rsync file transfers + K8s secret application
    airflow_image.sh     # Airflow Docker build + K3S import
    kafka.sh             # Kafka deploy
    mlflow.sh            # MLflow deploy + artifact root fix + port-forward
    flask.sh             # Flask build, ECR push, pod restart, readiness check
    airflow_pods.sh      # Helm upgrade, Airflow pod restarts (parallel), ml-venv setup
```

---

## How the fix solved the problem

| Scenario | Before | After |
|----------|--------|-------|
| Only DAG files changed | ~22 min (full deploy) | ~5-7 min (`--dags-only`) |
| Dockerfile/values changed | ~22 min | ~10-12 min (parallel Kafka+MLflow+build) |
| Kafka + MLflow deploy | ~12-15 min sequential | ~7-10 min parallel |
| Airflow pod waits | up to 18 min sequential | up to 6 min parallel |
| Airflow Docker build (warm cache) | 2-5 min (no-cache) | 10-30s (layer cache) |

The parallelization is semantically correct because the dependency graph is:
- Kafka, MLflow, Airflow build → all independent → run in parallel
- Helm upgrade → depends on Airflow build completing (needs image in K3S)
- Pod restarts → depend on Kafka + MLflow being up (DAGs connect to both)
- ml-venv → depends on pods being Ready

These constraints are enforced by the placement of `_wait_bg` calls before each dependent step.
