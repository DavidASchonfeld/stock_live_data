# deploy.sh — End-of-Run Warning & Error Summary

**Date:** 2026-04-10
**File affected:** `scripts/deploy.sh`

## Problem

`deploy.sh` is a ~800-line script spanning 8 deployment steps. Each step runs SSH commands, Docker builds, Helm upgrades, kubectl rollouts, pip installs, and Python heredocs. Warnings and errors from these tools — Kafka rollout timeouts, pip DEPRECATION notices, Python DeprecationWarning, Helm image mismatch warnings, Flask pod timeout warnings — were printed inline throughout hundreds of lines of log output. After a long deploy, these were easy to miss. There was no way to tell at a glance whether the deploy finished cleanly or left something to investigate.

## How It Was Identified

During repeated deployments while integrating Kafka, MLflow, and the ML anomaly detector, post-deploy debugging sessions revealed warnings that had been present in earlier runs but weren't noticed until they caused downstream failures. For example:
- A `WARNING: Helm did not update scheduler image` had been printed mid-output but was missed, causing the next DAG run to use a stale image.
- pip `DEPRECATION:` lines from `ml-venv` installs surfaced as actual breakages later.

The need for a consolidated summary became clear.

## Fix

Two additions at the top of `scripts/deploy.sh`, right after `set -euo pipefail`:

**1. Tee all output to a fixed logfile:**
```bash
DEPLOY_LOGFILE="/tmp/deploy-last.log"
exec > >(tee "$DEPLOY_LOGFILE") 2>&1
```
This redirects all stdout and stderr through `tee`, which writes to the terminal in real time AND saves everything to `/tmp/deploy-last.log`. The fixed filename ensures only the most recent deploy is kept.

**2. EXIT trap that greps the logfile:**
```bash
_print_deploy_summary() { ... }
trap '_print_deploy_summary' EXIT
```
The trap fires on every exit — normal completion, `set -e` abort, or explicit `exit 1`. It greps the logfile for known warning/error patterns and prints a deduplicated summary box at the bottom of the output, along with a clear `DEPLOY COMPLETE` or `DEPLOY FAILED` status line.

## Why This Approach

| Alternative | Why rejected |
|---|---|
| Manually append to arrays inside each `\|\| {}` block | Only catches warnings the script author explicitly tracks; misses external tool output (pip, kubectl, docker deprecations) |
| `grep` the terminal scrollback after the fact | Not reproducible; requires manual effort every time |
| Separate log file only, no summary | Still requires manually opening the file |
| `tee` + `EXIT trap` + `grep` | Catches **all** output automatically, works even on early exits, requires no per-step maintenance |

The `EXIT trap` pattern is the standard shell technique for guaranteed cleanup/reporting code. `tee` is idiomatic for simultaneous live display + file capture. Together they require zero per-step changes while covering every warning source.

## How It Solved the Problem

At the end of every deploy (success or failure), the terminal now shows:

```
==================================================================
  DEPLOY COMPLETE
  -- Warnings & Errors -------------------------------------------
    > WARNING: Helm did not update scheduler image — force-patching StatefulSet...
    > DEPRECATION: pip's legacy resolver...
    > ⚠ airflow not installed locally — skipping import validation

  Script ran to completion despite the above — review before closing.
==================================================================
  Full log: /tmp/deploy-last.log
==================================================================
```

Or on failure:
```
==================================================================
  DEPLOY FAILED  (exit code: 1)
  -- Warnings & Errors -------------------------------------------
    > ERROR: MLflow rollout timed out. Diagnosing...
  Script exited with errors — check items above and logs for details.
==================================================================
```

Nothing is missed. The full log is always available at `/tmp/deploy-last.log` for deeper inspection.
