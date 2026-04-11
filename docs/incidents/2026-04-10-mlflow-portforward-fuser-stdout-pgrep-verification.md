# Incident: MLflow Port-Forward — fuser stdout Corrupts Summary + pgrep Gives False Negatives

**Date:** 2026-04-10
**Severity:** Low (warning only; deploy completed successfully)
**Status:** Resolved
**File affected:** `scripts/deploy/mlflow.sh` — `step_mlflow_portforward()`

---

## What Happened

Every full deploy ended with this in the warning summary:

```
>  693331WARNING: port-forward may not have started. kubectl output from /tmp/mlflow-portforward.log:
```

Two bugs produced one symptom: a garbage PID prefix on the warning line, and a false negative
from the verification check. The MLflow UI at `localhost:5500` was unreachable after every deploy.

---

## How It Was Identified

The `693331` prefix on `WARNING:` was a dead giveaway that a raw number was being concatenated
without a line break. Reading `step_mlflow_portforward()` in `mlflow.sh` showed that
`fuser -k 5500/tcp 2>/dev/null || true` was the only place a raw number could be printed.
Checking `fuser` documentation confirmed it writes killed PIDs to stdout (not stderr) with no
trailing newline by default. The `2>/dev/null` redirect was silencing stderr but leaving stdout
untouched — so the PID flowed directly into the `tee` deploy log.

The `pgrep` false negative was identified by reasoning about timing: a single `sleep 3` then one
`pgrep` check gives kubectl a narrow window. kubectl port-forward can start, fail to reach the
MLflow endpoint, and exit — all within 3 seconds — leaving nothing for `pgrep` to find. The log
file is a more reliable signal because kubectl only writes `Forwarding from` when it has actually
started forwarding.

---

## Root Cause 1 — fuser stdout not suppressed (the `693331` prefix)

`fuser -k 5500/tcp 2>/dev/null || true` redirects stderr only. On Linux, `fuser -k` writes the
killed PID (e.g. `693331`) to **stdout** with **no trailing newline**. The `tee` pipeline in
`deploy.sh` writes all SSH heredoc output to `/tmp/deploy-last.log`. When `_print_deploy_summary`
greps the log for `WARNING`, the PID and the next `echo 'WARNING:...'` text appear as one merged
line — `693331WARNING: port-forward may not have started...` — because there is no newline
separating them.

---

## Root Cause 2 — `pgrep` checks process existence, not forwarding success

`pgrep -f 'kubectl port-forward svc/mlflow'` returns true if the process is in the process table.
It cannot tell whether kubectl is actually forwarding. kubectl port-forward prints
`Forwarding from 127.0.0.1:5500 -> 5500` only after it has bound the port **and** confirmed the
remote endpoint is reachable. If the service is not ready or the port is still held, kubectl logs
an error and exits — often within the 3-second window — leaving `pgrep` nothing to find.
A single 3-second sleep before a single check was a race condition against a fast-exiting process.

---

## Fix

Two changes inside the SSH heredoc in `step_mlflow_portforward()`:

### 1. Suppress fuser stdout

```bash
# Before
fuser -k 5500/tcp 2>/dev/null || true

# After — redirect stdout too so the killed PID doesn't bleed into the deploy log
fuser -k 5500/tcp >/dev/null 2>&1 || true
```

`>/dev/null 2>&1` redirects both stdout and stderr to `/dev/null`. The killed PID no longer
enters the deploy log and cannot merge with the next line.

### 2. Replace pgrep with log-file check inside a retry loop

```bash
# Before
sleep 3
if pgrep -f 'kubectl port-forward svc/mlflow' > /dev/null 2>&1; then
    echo '  port-forward running OK'
else
    echo 'WARNING: ...'
    cat /tmp/mlflow-portforward.log ...
fi

# After
_pf_ok=0
for _attempt in 1 2 3; do
    sleep 3
    if grep -q 'Forwarding from' /tmp/mlflow-portforward.log 2>/dev/null; then
        echo "  port-forward running OK (attempt $_attempt)"
        _pf_ok=1
        break
    fi
done
if [[ $_pf_ok -eq 0 ]]; then
    echo 'WARNING: port-forward may not have started. kubectl output from /tmp/mlflow-portforward.log:'
    cat /tmp/mlflow-portforward.log 2>/dev/null || echo '  (log file empty or missing)'
fi
```

`Forwarding from` is kubectl's own success signal — it only writes this line when the port is
bound and the endpoint is confirmed reachable. The retry loop (3 attempts × 3s = up to 9s total)
handles slow cluster restarts without adding a fixed worst-case sleep to every deploy. On a fast
cluster, the loop exits after the first 3-second wait — same latency as before.

---

## Why This Fix Works

- **`>/dev/null 2>&1`** on `fuser` prevents the killed PID (stdout) and any error text (stderr)
  from entering the deploy log. The summary grep no longer sees merged lines.

- **`grep -q 'Forwarding from'`** uses kubectl's own log as the source of truth instead of the
  OS process table. `pgrep` can miss a fast-exiting kubectl or give a false positive on a process
  that is running but not yet forwarding. The log file reflects what kubectl itself declared.

- **Retry loop** avoids both a fixed worst-case sleep and a single-shot race. On the happy path
  the loop exits as soon as kubectl writes `Forwarding from`, which is typically within 3 seconds.

---

## Relationship to Prior Port-Forward Incidents

| Prior incident | Problem | Fix then | Gap left |
|---|---|---|---|
| `2026-04-10-deploy-sh-portforward-exit255-silent-failure.md` | SSH exit 255 silently aborted deploy | Made step non-fatal with `return 0` | fuser stdout, pgrep unreliable |
| `2026-04-10-mlflow-portforward-ssh-exit255-three-calls.md` | 3 SSH calls, any could time out after long deploy | Consolidated into single heredoc session | fuser stdout, pgrep unreliable |
| `2026-04-10-mlflow-portforward-pgrep-warning.md` | sleep 2 too short, no inline log on failure | sleep 3, added `cat` of log inline | fuser stdout still leaking, pgrep still unreliable |
| **This incident** | fuser PID prefix corrupts summary line; pgrep races against fast-exiting kubectl | `>/dev/null 2>&1` on fuser; log-file check with retry loop | — |
