# Incident: deploy.sh — Two Bugs: ml-venv Shell Quoting + MLflow SQLite UNIQUE Constraint

**Date:** 2026-04-10
**Severity:** Blocking (ml-venv not created; MLflow experiment artifact root never fixed)

---

## Errors

**Bug 1 — Step 7b:**
```
./scripts/deploy.sh: line 547: 75             requests: No such file or directory
WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved.
```

**Bug 2 — Step 7c:**
```
Soft-deleted experiment 1 (had: /mlflow-data/artifacts/1)
mlflow.exceptions.RestException: RESOURCE_ALREADY_EXISTS: Experiment(name=anomaly_detection) already exists.
Error: (sqlite3.IntegrityError) UNIQUE constraint failed: experiments.workspace, experiments.name
```

---

## How They Were Encountered

Both errors appeared in sequence during a routine `./scripts/deploy.sh` run. Step 7b printed its WARNING and Step 7c crashed with a Python traceback before the deploy finished.

---

## Bug 1 — Step 7b: Shell Quoting (Inner Double-Quotes Break SSH String)

### Root Cause

Step 7b runs inside a double-quoted SSH command string: `ssh "$EC2_HOST" "..."`. Inside that string, the pip install package specs were also wrapped in double-quotes — e.g., `"mlflow==2.15.1"`. In bash, double-quoted strings cannot contain unescaped double-quotes; the first inner `"` terminates the outer string early.

This left `setuptools<75` as an unquoted bare token in the shell. The `<` operator is a file redirect in bash, so `<75` was interpreted as "read stdin from a file named `75`". That file doesn't exist — hence `line 547: 75  requests: No such file or directory`. None of the pip packages were actually installed.

### How It Was Identified

The error message `75  requests: No such file or directory` looked like a file-not-found error. Tracing back to the `setuptools<75` package spec revealed the `<` was being treated as shell redirection. The cause was the outer double-quoted SSH string having inner double-quoted package specs — a classic bash quoting nesting bug.

### Fix

Escaped every inner double-quote with `\"`:
```bash
\"mlflow==2.15.1\" \
\"scikit-learn==1.5.2\" \
\"setuptools<75\" \
\"requests>=2.32.0\" \
```

### Why This Fix

Minimal, targeted change. The outer double-quoted SSH string is preserved as-is (consistent with the rest of the deploy script). Escaping `\"` is the standard bash fix for embedding double-quotes inside a double-quoted string. No structural changes needed.

### How It Solved the Problem

Bash now sees the full, unbroken outer string. The package specs are passed literally to `pip install` on the remote shell, and `<` is never exposed as a bare shell token.

---

## Bug 2 — Step 7c: MLflow SQLite UNIQUE Constraint Blocks `create_experiment`

### Root Cause

Step 7c was trying to fix the `anomaly_detection` experiment's artifact root from an old local path (`/mlflow-data/artifacts/1`) to the HTTP-proxied URI (`mlflow-artifacts:/`). Its approach:
1. Soft-delete the experiment via `client.delete_experiment()`
2. Call `client.create_experiment('anomaly_detection', artifact_location='mlflow-artifacts:/')`

Step 2 failed with `RESOURCE_ALREADY_EXISTS`. The reason: MLflow's SQLite backend stores soft-deleted experiments as rows with `lifecycle_stage='deleted'` — the row is never removed. The schema has a UNIQUE constraint on `(workspace, name)` across **all** rows regardless of lifecycle stage. So after the soft-delete, the name `anomaly_detection` was still occupied in SQLite, and `create_experiment` hit the constraint.

### How It Was Identified

The traceback pointed directly to `create_experiment` and the `sqlite3.IntegrityError: UNIQUE constraint failed: experiments.workspace, experiments.name`. Checking the MLflow SQLite schema confirmed that soft-delete only flips `lifecycle_stage`; it does not free the unique name slot. The deploy log showed one experiment was deleted just before the failure, confirming the delete-then-create sequence was the trigger.

### Fix

Replaced the delete + create approach with a direct `sqlite3` UPDATE inside the MLflow pod:

```bash
kubectl exec -n airflow-my-namespace deployment/mlflow -- python3 -c "
import sqlite3, time, sys
db = sqlite3.connect('/mlflow-data/mlflow.db')
row = db.execute(\"SELECT experiment_id, artifact_location, lifecycle_stage FROM experiments WHERE name='anomaly_detection'\").fetchone()
if row is None:
    print('No anomaly_detection experiment found — nothing to fix')
    db.close(); sys.exit(0)
exp_id, art_loc, stage = row
if art_loc == 'mlflow-artifacts:/' and stage == 'active':
    print(f'Experiment {exp_id} already correct — skipping')
    db.close(); sys.exit(0)
db.execute(
    \"UPDATE experiments SET artifact_location='mlflow-artifacts:/', lifecycle_stage='active', last_update_time=? WHERE name='anomaly_detection'\",
    (int(time.time() * 1000),)
)
db.commit(); db.close()
print(f'Fixed {exp_id}: root={art_loc} → mlflow-artifacts:/, stage={stage} → active')
"
```

The PYEOF Python block (which uses the MLflow HTTP API) was kept as the diagnostic/skip-check step; it now logs current state and exits early if the root is already correct. The actual fix is the sqlite3 UPDATE that follows.

### Why This Fix

The MLflow Python API has no method to update an experiment's `artifact_location` after creation, and `create_experiment` cannot reuse a soft-deleted name. The only escape is to bypass the API and write directly to the SQLite DB. Since the MLflow container has Python's stdlib `sqlite3` module and the DB is at the known path `/mlflow-data/mlflow.db` (from the deployment manifest), this is clean and self-contained. It also doubles as a restore: setting `lifecycle_stage='active'` in the same UPDATE means a previously soft-deleted experiment is recovered correctly with no separate API call.

### How It Solved the Problem

The UPDATE modifies the existing row in-place — no insert, no UNIQUE constraint. After the fix, the `anomaly_detection` experiment is active with `artifact_location='mlflow-artifacts:/'`. The anomaly_detector.py's `set_experiment()` finds a live experiment with the correct root, and MLflow artifact logging routes through the HTTP proxy rather than trying to write to a stale local path.
