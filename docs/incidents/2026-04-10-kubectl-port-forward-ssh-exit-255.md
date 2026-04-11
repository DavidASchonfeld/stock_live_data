# Incident: Step 7d Deploy Failed — exit code 255 (kubectl port-forward via SSH)

**Date:** 2026-04-10
**Component:** `scripts/deploy/mlflow.sh` — `step_mlflow_portforward`
**Severity:** High — deploy always fails at Step 7d; MLflow UI unreachable

---

## What Happened

Running `./scripts/deploy.sh` failed every time at Step 7d with exit code 255:

```
=== Step 7d: Starting kubectl port-forward for MLflow UI (EC2 localhost:5500) ===

==================================================================
  DEPLOY FAILED  (exit code: 255)
  Elapsed time: 19m 11s
  -- Warnings & Errors -------------------------------------------
  (none)
==================================================================
```

No warnings or errors were printed — the failure came from the SSH client itself, not from any application-level error.

---

## How It Was Identified

The deploy summary showed exit code 255 with no application-level warnings. SSH exit code 255 specifically means the SSH client failed to close cleanly (it is not a code returned by remote commands in normal operation). This pointed to a problem with the SSH session itself rather than with kubectl or the Kubernetes cluster.

The offending line was the `nohup kubectl port-forward ... &` command:

```bash
ssh "$EC2_HOST" "nohup kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 \
    --address=127.0.0.1 > /tmp/mlflow-portforward.log 2>&1 &"
```

---

## Root Cause

When SSH runs a remote command, it keeps the connection open until all file descriptors on the remote side are closed. Backgrounding with `&` causes the shell to exit immediately, but the **background process (`kubectl port-forward`) inherits the SSH channel's open stdin file descriptor**.

Even though `kubectl port-forward` never reads from stdin, that open fd is enough to keep the SSH multiplexer alive. The SSH client then waits indefinitely for EOF on the channel, eventually times out, and exits with code 255.

This is the standard SSH + background process pitfall: stdout and stderr were correctly redirected to a file (`> /tmp/... 2>&1`), but **stdin was never closed**, leaving one channel still tied to the SSH session.

---

## Fix

Added `</dev/null` to the remote command to explicitly close kubectl's stdin, fully detaching the background process from the SSH session:

```bash
# Before (broken)
ssh "$EC2_HOST" "nohup kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 \
    --address=127.0.0.1 > /tmp/mlflow-portforward.log 2>&1 &"

# After (fixed)
ssh "$EC2_HOST" "nohup kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 \
    --address=127.0.0.1 </dev/null > /tmp/mlflow-portforward.log 2>&1 &"
```

**File changed:** `scripts/deploy/mlflow.sh` — `step_mlflow_portforward`

---

## Why This Fix

With all three file descriptors redirected (`</dev/null` for stdin, `>/tmp/...` for stdout, `2>&1` for stderr), the background process has no connection to the SSH channel. SSH detects that all channels are closed and exits cleanly with code 0. The background `kubectl port-forward` continues running on EC2 independently.

This is the canonical pattern for launching daemons over SSH: ensure stdin, stdout, and stderr are all explicitly redirected before backgrounding.

---

## Verification

1. Run `./scripts/deploy.sh`
2. Step 7d should complete and print: `port-forward running OK`
3. Open SSH tunnel: `ssh -L 5500:localhost:5500 ec2-stock`
4. Navigate to `http://localhost:5500` — MLflow UI should load
