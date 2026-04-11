# Incident: MLflow Port-Forward SSH Exit 255 — Three Separate SSH Calls After Long Deploy

**Date:** 2026-04-10
**Severity:** Low (warning only; deploy completed successfully)
**File affected:** `scripts/deploy/mlflow.sh` — `step_mlflow_portforward()`

---

## What Happened

At the end of a ~14-minute deploy, the summary printed:

```
WARNING: MLflow port-forward reset failed (SSH exit 255) — tunnel may need manual restart via: ...
```

The MLflow UI at `localhost:5500` was unreachable until the port-forward was restarted manually.

---

## Root Cause

`step_mlflow_portforward()` made **three separate SSH connections** to EC2, in sequence:

1. `ssh EC2 "pkill -f 'kubectl port-forward svc/mlflow' || true"` — kill stale process
2. `ssh EC2 "nohup kubectl port-forward ... &"` — start new port-forward
3. `ssh EC2 "pgrep ... && echo OK"` — verify it started

After a 14-minute deploy, the SSH ControlMaster socket (or underlying TCP connection) had expired. The **first** SSH call — the `pkill` — failed immediately with exit 255 (SSH connection refused or timed out). The `|| { ... return 0 }` block caught this and returned early. Calls 2 and 3 were never reached, so the port-forward was never restarted.

Each SSH call required its own fresh connection. Any one of those three could independently fail; in practice, the first one hit the timeout ceiling and aborted the whole step.

---

## How It Was Identified

The warning message appeared in the deploy summary. The message text said "reset failed (SSH exit 255)", which pointed to the `pkill` step specifically. Reading `step_mlflow_portforward()` in `mlflow.sh` confirmed three separate `ssh` calls, each with its own `|| { return 0 }` bail-out. The first bail-out was where execution stopped.

---

## Fix

Consolidated all three SSH calls into **one SSH session** using a heredoc:

```bash
_pf_exit=0
ssh -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
    "$EC2_HOST" bash << 'REMOTE' || _pf_exit=$?
pkill -f 'kubectl port-forward svc/mlflow' || true
sleep 1
nohup kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 \
    --address=127.0.0.1 </dev/null > /tmp/mlflow-portforward.log 2>&1 &
sleep 2
pgrep -f 'kubectl port-forward svc/mlflow' && echo '  port-forward running OK' \
    || echo 'WARNING: ...'
REMOTE
if [[ $_pf_exit -ne 0 ]]; then
    echo "WARNING: MLflow port-forward setup failed (SSH exit $_pf_exit) — ..."
    return 0
fi
```

Added SSH options:
- `-o ConnectTimeout=15` — hard 15s timeout on connection attempt
- `-o ServerAliveInterval=5 -o ServerAliveCountMax=3` — keepalives every 5s during the `sleep` pauses, dropping after 15s of silence

---

## Why This Fix Works

**One handshake instead of three.** If the SSH connection can be established at all, all three operations (kill → start → verify) run inside the same session without re-connecting. The kill step failing to SSH is no longer different from the start step failing to SSH — they share the same connection fate.

**Keepalives prevent mid-session drops.** The `sleep 1` and `sleep 2` pauses inside the heredoc previously required the connection to stay idle. `ServerAliveInterval=5` sends a probe every 5 seconds, keeping the connection alive through those pauses.

**`|| _pf_exit=$?` handles `set -e` correctly.** The `|| assignment` pattern prevents `set -e` from aborting the script on SSH failure, while still capturing the exit code for the warning message. The `_pf_exit` variable name is prefixed with `_pf_` to avoid colliding with other variables in the sourced environment.

**`</dev/null` on kubectl is preserved.** Inside the heredoc the remote bash script still redirects kubectl's stdin to /dev/null. This is critical: without it, kubectl holds the SSH channel's stdin open and prevents the session from exiting cleanly — the exact behavior that originally caused exit 255 on the old multi-call design.
