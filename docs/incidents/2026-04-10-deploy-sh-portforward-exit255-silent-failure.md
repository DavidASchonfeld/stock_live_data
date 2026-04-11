# Incident: deploy.sh — Step 7d Exit 255 + Silent "(none)" Warning Summary

**Date:** 2026-04-10  
**Severity:** Low (deploy succeeds up to this point; only MLflow UI tunnel affected)  
**Files changed:** `scripts/deploy/mlflow.sh`, `scripts/deploy.sh`, `scripts/deploy/common.sh`

---

## What Happened

`./scripts/deploy.sh` failed at the very end with:

```
=== Step 7d: Starting kubectl port-forward for MLflow UI (EC2 localhost:5500) ===

==================================================================
  DEPLOY FAILED  (exit code: 255)
  Elapsed time: 12m 11s
  -- Warnings & Errors -------------------------------------------
  (none)
==================================================================
```

The entire deploy had succeeded (Airflow, Kafka, MLflow, Flask, all pod restarts). Only the
final UI-convenience step failed. The Warnings & Errors section showed **(none)**, which made
the failure look like a phantom with no information to debug.

---

## Root Cause

### Why exit 255?

SSH returns exit code 255 specifically when the SSH **connection itself** fails — not when the
remote command fails. After a 12-minute deploy the SSH control socket can time out or the
connection can drop transiently.

In `step_mlflow_portforward` (`mlflow.sh`), the first SSH call was:

```bash
ssh "$EC2_HOST" "pkill -f 'kubectl port-forward svc/mlflow' || true"
```

The `|| true` is **inside the quoted string** — it runs on the remote EC2 side and only
suppresses pkill's own non-zero exit when no process was found. It does **not** catch a
failure of the `ssh` command itself on the local side. When SSH returns 255, the local shell
sees a non-zero exit and, because `set -euo pipefail` is active in deploy.sh, immediately
aborts the entire script.

### Why "(none)" in the warnings?

`_print_deploy_summary` searches the log for keyword patterns (`WARNING`, `ERROR`, `⚠`, etc.).
An SSH connection failure produces either no output or an SSH-level error message that doesn't
match any of those keywords. So grep found nothing and printed "(none)", hiding the failure
entirely.

---

## How It Was Identified

1. Observed "DEPLOY FAILED (exit code: 255)" with "(none)" warnings — obviously contradictory.
2. Read `step_mlflow_portforward` in `mlflow.sh` — noticed `|| true` was scoped to the remote
   shell, not the local `ssh` invocation.
3. Confirmed SSH exit 255 = connection failure, not a kubectl error.
4. Noted the port-forward step is purely a UI convenience (the SSH tunnel for MLflow); the
   underlying deploy was already complete by this point.

---

## Fix

### Fix 1 — Make `step_mlflow_portforward` non-fatal (`mlflow.sh`)

Added a local `|| { echo "WARNING: ..."; return 0; }` block after each SSH call. If SSH fails,
the step prints an actionable warning (including the manual restart command) and returns
successfully instead of propagating exit 255.

This is the correct fix because the port-forward is **UI-only**. The deploy doesn't need it to
succeed — the Airflow DAGs connect to MLflow via the in-cluster DNS address, not via this
port-forward.

### Fix 2 — Capture the failing command (`deploy.sh`)

Added a global `DEPLOY_FAILED_CMD` variable and an ERR trap:

```bash
DEPLOY_FAILED_CMD=""
trap 'DEPLOY_FAILED_CMD="$BASH_COMMAND"' ERR
```

The ERR trap fires on every non-zero exit before the EXIT trap runs, recording exactly which
bash command failed. The summary now prints it under "DEPLOY FAILED".

### Fix 3 — Show last 15 log lines when no keywords match (`common.sh`)

When exit_code is non-zero but the grep for WARNING/ERROR keywords finds nothing, the summary
now tails the last 15 lines of the log instead of printing "(none)". This ensures there is
always a visible diagnostic trail even for failures that produce no keywords.

---

## How the Fix Solves the Problem

- **Exit 255 won't abort the deploy** — the step degrades gracefully and the deploy completes.
- **The WARNING it prints** now appears in the Warnings & Errors section (the grep pattern
  matches `WARNING:`), so it's visible without digging into the log.
- **Future silent failures** at any step will now show the exact failing command and the last
  15 log lines, making them immediately actionable.
