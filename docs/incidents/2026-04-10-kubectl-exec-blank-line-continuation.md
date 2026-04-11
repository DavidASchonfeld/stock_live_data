# Incident: `kubectl exec` — Blank Line After Backslash Breaks Shell Continuation

**Date:** 2026-04-10
**Severity:** Low — no data loss, no pod crash; blocked post-deploy verification only

---

## Errors

```
error: you must specify at least one command for the container
airflow: command not found
```

---

## How It Was Encountered

During verification step 6 of the anomaly detection pipeline integration, immediately after running `./scripts/deploy.sh`. The command was entered in the terminal with a blank line between the backslash continuation and the `airflow tasks list` argument:

```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \

    airflow tasks list stock_consumer_pipeline
```

Note the blank line between `--\` and `airflow tasks list`. The result was two separate errors: kubectl printed "you must specify at least one command for the container", and then the shell tried to run `airflow` as a local binary and printed "airflow: command not found".

---

## Root Cause

In POSIX shells (bash, zsh), `\` followed **immediately** by a newline is a line continuation — the `\<newline>` pair is removed and the next line is joined to the current command as if there were no line break. A blank line inserts **two** newlines. The first newline terminates the `\` continuation; the second newline terminates the now-empty continuation line, which ends the command.

This means:

- `kubectl exec ... --` ran with no arguments after `--` → kubectl error: "you must specify at least one command"
- `    airflow tasks list stock_consumer_pipeline` became a separate command in the local shell → shell error: "airflow: command not found" (the `airflow` binary is inside the container, not on the host)

---

## How It Was Identified

The two error messages were the key signal:

1. "you must specify at least one command for the container" — kubectl received zero arguments after `--`, meaning the continuation was broken before `airflow tasks list` was appended
2. "airflow: command not found" — the shell tried to execute `airflow` as a local binary, confirming the second "line" ran as a separate local command

Cross-referencing the entered command with POSIX continuation rules revealed the blank line as the cause immediately.

---

## Fix

Remove the blank line. `\` must be immediately followed by a newline with no intervening whitespace or empty lines:

```bash
# Before (broken — blank line after \):
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \

    airflow tasks list stock_consumer_pipeline

# After (correct — no blank line):
kubectl exec airflow-scheduler-0 -n airflow-my-namespace \
    -- airflow tasks list stock_consumer_pipeline
```

---

## Why This Fix

`\<newline>` is a single token: the backslash escapes the newline and the shell removes both characters, joining the lines. A blank line between `\` and the next argument inserts a second `\n` that the shell sees as a command terminator before it ever reaches `airflow tasks list`. Moving `--` to the same line as the continuing content ensures all arguments are part of the same command.

---

## How the Fix Solved the Problem

With no blank line, the shell joins `kubectl exec airflow-scheduler-0 -n airflow-my-namespace` and `-- airflow tasks list stock_consumer_pipeline` into a single command. kubectl receives `airflow tasks list stock_consumer_pipeline` as the container command and executes it inside the pod, where the `airflow` binary exists at `/home/airflow/.local/bin/airflow`.
