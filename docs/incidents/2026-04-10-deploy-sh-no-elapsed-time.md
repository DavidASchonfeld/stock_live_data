# Incident: deploy.sh Had No Elapsed-Time Display

**Date:** 2026-04-10
**Severity:** Low (quality-of-life gap, no functional failure)

## What Caused It

`deploy.sh` already printed a warning/error summary at the end of every run, but it never reported how long the deploy took. There was no way to tell at a glance whether a run finished in 2 minutes or 15 — you had to mentally track the start time yourself or dig through the log timestamps.

## How It Was Encountered and Identified

Noticed during a routine deploy review: the summary block showed DEPLOY COMPLETE with warnings listed, but gave no timing information. Slow deploys (e.g., image builds, Helm upgrades) were indistinguishable from fast ones without external tooling.

## How It Was Fixed

Two small additions to `scripts/deploy.sh`:

1. **Capture start time** — immediately after the `exec > >(tee ...)` logfile setup line, store bash's built-in `$SECONDS` counter:
   ```bash
   DEPLOY_START=$SECONDS
   ```

2. **Print elapsed time in `_print_deploy_summary`** — before the Warnings & Errors divider, compute and print the duration:
   ```bash
   local elapsed=$(( SECONDS - DEPLOY_START ))
   local elapsed_min=$(( elapsed / 60 ))
   local elapsed_sec=$(( elapsed % 60 ))
   printf "  Elapsed time: %dm %02ds\n" "$elapsed_min" "$elapsed_sec"
   ```

## Why This Fix Was Chosen

`$SECONDS` is a bash built-in that auto-increments every second since the shell started — no `date` subprocess, no arithmetic on epoch strings, no portability concerns. It's the idiomatic, zero-overhead way to measure script wall-clock time in bash.

Placing the output inside the existing `_print_deploy_summary` trap function (which already runs on every exit, success or failure) means elapsed time is always shown regardless of how the script exits — clean finish, early abort, or `set -e` failure.

## How the Fix Solved the Problem

The summary block now looks like:

```
==================================================================
  DEPLOY COMPLETE
  Elapsed time: 3m 42s
  -- Warnings & Errors -------------------------------------------
  (none)
==================================================================
```

Elapsed time is visible at a glance alongside the pass/fail status, making it easy to spot regressions in deploy speed without any external timing tools.
