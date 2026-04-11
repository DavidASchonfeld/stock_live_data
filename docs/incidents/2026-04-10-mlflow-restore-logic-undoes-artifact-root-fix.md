# Incident: MLflow Restore Logic Reviving Stale Local-Path Experiment

**Date:** 2026-04-10
**Severity:** Blocking (PermissionError crashes `anomaly_detector.py` on every run)

---

## Error

```
PermissionError: [Errno 13] Permission denied: '/mlflow-data'
  mlflow.store.artifact.local_artifact_repo.LocalArtifactRepository.log_artifacts
```

---

## How It Was Encountered

Post-deploy manual verification — same command as the previous artifact-root incident:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    /opt/ml-venv/bin/python /opt/airflow/dags/anomaly_detector.py \
    --contamination 0.05 --n-estimators 100
```

The crash was the same `PermissionError` as in the prior incident, despite deploy.sh's step 7c having been added specifically to fix it. The MLflow run URL in stdout showed `experiments/1` — the original experiment — rather than the newly-created one.

---

## Root Cause

Two interacting bugs:

**Bug 1 — `anomaly_detector.py` unconditionally restored the broken experiment**

`mlflow.tracking.MlflowClient.get_experiment_by_name` searches ALL experiments, including soft-deleted ones. Step 7c had soft-deleted exp_id=1 (which had `artifact_location=/mlflow-data/artifacts`) and created exp_id=2 (`mlflow-artifacts:/`). But on the next `anomaly_detector.py` run, `get_experiment_by_name("anomaly_detection")` returned exp_id=1 (the first match by ID, soft-deleted). The restore block:

```python
if _exp is not None and _exp.lifecycle_stage == "deleted":
    _client.restore_experiment(_exp.experiment_id)  # restored the broken one!
```

...unconditionally revived exp_id=1, putting the stale local-path artifact root back into active use. `set_experiment` then used exp_id=1, and all runs under it inherited the broken `artifact_location`.

**Bug 2 — step 7c only searched active experiments, never cleaned up soft-deleted ones**

`search_experiments()` defaults to `ViewType.ACTIVE_ONLY`. On re-deploys, if exp_id=1 had already been soft-deleted by a prior step 7c run, the search returned nothing, `create_experiment` created a new active experiment — but exp_id=1 remained in the database, ready to be revived by Bug 1 on the very next script execution.

Additionally, step 7c used `|| echo "WARNING: ..."` which silently swallowed failures, making it impossible to tell whether the reset had actually succeeded.

---

## How It Was Identified

The `experiments/1` in the MLflow run URL was the key signal — it proved the old experiment was still being used despite step 7c running. The `local_artifact_repo` class name in the traceback confirmed the artifact URI was still a local path, not a proxy URI. Cross-referencing the restore block in `anomaly_detector.py` with the fact that `get_experiment_by_name` searches all experiments (not just active) revealed the mechanism.

---

## Fix

**`airflow/dags/anomaly_detector.py`** — guarded the restore with an `artifact_location` check:

```python
# Before: restores any soft-deleted experiment regardless of its artifact root
if _exp is not None and _exp.lifecycle_stage == "deleted":
    _client.restore_experiment(_exp.experiment_id)

# After: only restore if artifact root is the HTTP proxy
if _exp is not None and _exp.lifecycle_stage == "deleted":
    # Only restore if artifact root is the HTTP proxy — skip stale local-path experiments
    if _exp.artifact_location == "mlflow-artifacts:/":
        _client.restore_experiment(_exp.experiment_id)
```

**`scripts/deploy.sh`** — rewrote step 7c to be idempotent and fail loudly:

- Replaced the fragile triple-nested `python -c "..."` inside `ssh "..."` with a local heredoc piped to `kubectl exec -i ... -- python` (reads from stdin — no quoting issues)
- Made the reset a no-op if the active experiment already has `mlflow-artifacts:/` root (safe to re-run on every deploy)
- Added `ViewType.ALL` reporting so lingering soft-deleted experiments are surfaced
- Removed `|| echo "WARNING"` — step 7c now fails loudly so broken state is visible immediately

---

## Why This Fix

The restore guard approach targets the exact failure mode: the only reason to restore a soft-deleted experiment is if it had the correct proxy URI and was accidentally deleted. Experiments with a local-path root should stay deleted and let `set_experiment` pick up (or create) the active correct-root experiment instead.

Alternatives considered and rejected:
- **Permanently delete (hard-delete) exp_id=1** — MLflow's Python API doesn't expose hard-delete; only the server REST endpoint does. A simpler guard in the code is safer.
- **Remove the restore logic entirely** — if an experiment is accidentally deleted from the UI, the next run would create a fresh one and lose the run history association. The guard preserves that recovery path for valid cases.

---

## How the Fix Solved the Problem

With the guard in place, `get_experiment_by_name` still returns exp_id=1 (soft-deleted, wrong root), but the restore block now sees `artifact_location != "mlflow-artifacts:/"` and skips the restore. `set_experiment("anomaly_detection")` then finds exp_id=2 (active, `mlflow-artifacts:/`) and uses it. All new runs are created under exp_id=2, the MLflow client selects `MlflowArtifactRepository` (HTTP proxy) instead of `LocalArtifactRepository`, and model artifacts upload over HTTP to the server — no direct filesystem write from the scheduler pod.
