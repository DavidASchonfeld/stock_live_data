# Incident: `$1: unbound variable` crash in deploy.sh

**Date:** 2026-04-10
**Severity:** Deploy blocked (no production impact)

---

## Error

```
./scripts/deploy.sh: line 153: $1: unbound variable
```

Script aborted during Step 2b2 (building and importing the Airflow+dbt Docker image into K3S).

---

## Root Cause

`deploy.sh` runs with `set -euo pipefail`. The `-u` flag causes the shell to treat any reference to an unset variable as a fatal error.

On line 157, an `awk` command was embedded inside a **double-quoted** SSH string:

```bash
ssh "$EC2_HOST" "
    ...
    sudo k3s ctr images ls | grep 'airflow-dbt' | awk '{print $1}' | ...
"
```

Double-quoted strings in bash are expanded by the **local shell** before being sent over SSH. So `$1` — intended as an `awk` field reference on the remote host — was instead interpreted as the first positional argument to the local `deploy.sh` script. Since the script is invoked with no arguments, `$1` was unset, and `-u` aborted execution.

---

## How It Was Identified

The error message pointed directly to line 153 (the `ssh` call). Inspecting that block revealed the unescaped `$1` inside a double-quoted string — a classic shell quoting trap.

---

## Fix

Escaped `$1` as `\$1` so the local shell passes it through literally, and the remote shell receives it intact as an `awk` field reference:

```diff
- awk '{print $1}'
+ awk '{print \$1}'
```

**File:** `scripts/deploy.sh`, line 157

---

## Why This Fix

The minimal correct solution for embedding `awk` field references inside a double-quoted SSH string is to escape the `$`. This preserves the double-quoted string (which is needed so other variables like `$BUILD_TAG` and `$EC2_HOME` *do* expand locally) while protecting the `awk`-specific `$1` from local expansion.

An alternative would be switching to a heredoc (`ssh … << 'EOF'`), but that would require restructuring the entire SSH block and offers no practical advantage here.

---

## How the Fix Solved the Problem

With `\$1`, the local shell sees a literal `\$1` and passes `$1` (unescaped) to the remote shell. The remote shell then evaluates it correctly as `awk`'s first-field reference, and the image purge command runs as intended.
