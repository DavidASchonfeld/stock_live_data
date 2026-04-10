# Bug Report: Helm Upgrade Silently Failing in deploy.sh

## The Error

Every run of `./scripts/deploy.sh` printed this during Step 2d (Helm upgrade):

```
Error: "helm upgrade" requires 2 arguments
bash: line 2: --force: command not found
Note: Helm hook timed out (expected — post-upgrade job takes >2m; upgrade was applied).
```

The script continued and exited 0, so the failure was easy to miss.

---

## Root Cause

The `helm upgrade` command was written as a multiline string inside double quotes, passed over SSH, with an inline comment on one of the flag lines:

```bash
ssh "$EC2_HOST" "helm upgrade airflow apache-airflow/airflow \
    --atomic=false --timeout 10m \  # 10m: post-upgrade hook runs airflow db migrate...
    --force \
    -f $EC2_HELM_PATH/values.yaml"
```

In a normal shell script, bash strips `# comments` before executing. But here the entire command is a double-quoted string — bash does not interpret or strip comments inside double quotes. The raw text (including `# 10m: ...`) is sent verbatim to the remote shell via SSH.

On the remote side, the shell sees the `#` and treats everything after it as a comment, which ends that line. The `\` continuation after the comment was consumed as part of the comment text, so `--force` ended up isolated on its own line — where the shell tried (and failed) to execute it as a program.

Two things broke as a result:
1. `helm upgrade` received only one positional argument (`airflow`) instead of the required two (`airflow apache-airflow/airflow`), causing the "requires 2 arguments" error.
2. `--force` was never passed to helm at all.

---

## How It Was Encountered and Identified

During a deploy to fix an unrelated `pkg_resources` error in the Airflow scheduler pod, the full deploy output was read carefully. The Helm error lines stood out — the message claimed the upgrade was "applied", but the error above it showed it clearly was not. Looking at the `deploy.sh` source at those lines immediately revealed the inline comment sitting inside the SSH string.

---

## The Fix

The inline comment was removed from inside the double-quoted SSH string and placed above it instead, where bash can actually treat it as a comment. `--atomic=false` and `--timeout 10m` were also split onto separate lines for clarity:

```bash
# FIX: flags are on separate lines with no inline comments — inline comments inside a double-quoted SSH
# string are NOT stripped by bash; they become literal text, breaking argument parsing and leaving
# --force on its own line where the shell treats it as a separate command ("command not found").
ssh "$EC2_HOST" "helm upgrade airflow apache-airflow/airflow \
    -n airflow-my-namespace \
    --version 1.20.0 \
    --atomic=false \
    --timeout 10m \
    --force \
    -f $EC2_HELM_PATH/values.yaml" \
  || echo "Note: Helm hook timed out (expected — post-upgrade job takes >2m; upgrade was applied)."
```

---

## Why This Fix

Moving the comment outside the quoted string is the minimal correct fix — no flags removed, no logic changed. It restores exactly the `helm upgrade` call that was intended. Splitting `--atomic=false` and `--timeout` onto their own lines is a style improvement that also guards against the same mistake recurring on those flags.

---

## Why It Was Silent for So Long

The failure was masked in two ways:

1. The `|| echo` at the end suppressed the non-zero exit code, so `set -euo pipefail` never aborted the script.
2. Airflow pods were restarted in Step 7 regardless (pod delete + recreate), which picked up the new Docker image whether or not Helm applied values changes. Deploys that only changed DAG files or the Docker image appeared to work fine — only `values.yaml` changes were silently dropped.
