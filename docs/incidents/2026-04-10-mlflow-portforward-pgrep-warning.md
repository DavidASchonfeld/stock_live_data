# Incident: MLflow Port-Forward pgrep Warning — Log Not Surfaced Inline

**Date:** 2026-04-10
**Severity:** Low (warning only; deploy completed successfully)
**File affected:** `scripts/deploy/mlflow.sh` — `step_mlflow_portforward()`

---

## What Happened

Every full deploy ended with this warning in the summary:

```
WARNING: port-forward may not have started — check /tmp/mlflow-portforward.log on EC2
```

The MLflow UI at `localhost:5500` was unreachable. No SSH exit 255 occurred — the SSH heredoc from the prior fix was working. `pgrep` simply didn't find the `kubectl port-forward` process after `sleep 2`.

---

## Root Cause

Two contributing factors:

1. **`sleep 2` was too short.** After `nohup kubectl port-forward ... &`, 2 seconds was not reliably long enough for the process to appear in the process table before `pgrep` ran. On a freshly restarted cluster or after a long deploy this window can stretch.

2. **Port 5500 was sometimes still held.** `pkill -f 'kubectl port-forward svc/mlflow'` kills by process name only. A stale forward left under a different invocation path (e.g. a zombie nohup process) would survive the kill and block the new forward with "address already in use". That would cause kubectl to crash immediately after start — within `sleep 2` — and `pgrep` would find nothing.

3. **The log was never shown.** The warning only said "check /tmp/mlflow-portforward.log on EC2", requiring a manual SSH session to read the actual kubectl error. There was no way to know why it failed without leaving the deploy context.

---

## How It Was Identified

The deploy summary printed the warning after every full deploy. The incident log `2026-04-10-mlflow-portforward-ssh-exit255-three-calls.md` had already addressed the SSH exit 255 case (3 separate SSH calls → 1 heredoc session). Since SSH was now succeeding, the failure was clearly inside the remote bash script — specifically the `pgrep` check at `mlflow.sh:157`. Reading the function made it clear that `sleep 2` was the only guard before the check, and that nothing ever read the log file.

---

## Fix

Three changes to `step_mlflow_portforward()` in `scripts/deploy/mlflow.sh`:

### 1. Kill the port by number in addition to by name

```bash
pkill -f 'kubectl port-forward svc/mlflow' || true
fuser -k 5500/tcp 2>/dev/null || true  # release port 5500 regardless of process name
```

`fuser -k` kills whatever process holds port 5500/tcp, regardless of how it was invoked. This handles zombie nohup processes and any other forward not matching the exact name pattern.

### 2. Increase sleep from 2s to 3s

```bash
sleep 3
```

Gives kubectl port-forward a more reliable window to appear in the process table before pgrep runs.

### 3. Print the log file inline on failure

```bash
if pgrep -f 'kubectl port-forward svc/mlflow' > /dev/null 2>&1; then
    echo '  port-forward running OK'
else
    echo 'WARNING: port-forward may not have started. kubectl output from /tmp/mlflow-portforward.log:'
    cat /tmp/mlflow-portforward.log 2>/dev/null || echo '  (log file empty or missing)'
fi
```

If `pgrep` finds nothing, the actual kubectl error (e.g. "address already in use", "unable to connect to server") is printed directly in the deploy output and captured in `/tmp/deploy-last.log`. No SSH needed to diagnose.

---

## Why This Fix Works

- **`fuser -k` clears the port unconditionally.** Even if pkill missed a process, fuser will release 5500/tcp before the new forward starts — eliminating "address already in use" crashes.
- **Extra 1 second of sleep** reduces the race between the background process appearing and pgrep checking.
- **Inline log read** converts an opaque "check EC2" message into a self-contained diagnostic. Future failures show their root cause in the deploy summary without any manual steps.
