#!/bin/bash
# Module: common — shared variables, helpers, and deploy summary trap.
# Loaded by deploy.sh; assumes SCRIPT_DIR, PROJECT_ROOT, DEPLOY_LOGFILE, and DEPLOY_START have already been set.

# ── Load deploy secrets from .env.deploy ─────────────────────────────────────
# .env.deploy is gitignored and contains real AWS values (ECR registry, region).
# See .env.deploy.example for the template. This keeps AWS account IDs out of git.
ENV_DEPLOY="$PROJECT_ROOT/.env.deploy"

if [ ! -f "$ENV_DEPLOY" ]; then
    echo "ERROR: $ENV_DEPLOY not found."
    echo "Copy .env.deploy.example to .env.deploy and fill in your AWS values."
    echo "  cp .env.deploy.example .env.deploy"
    exit 1
fi

# shellcheck source=../../.env.deploy
source "$ENV_DEPLOY"

# Make sure the required variables were actually set in .env.deploy (in case the file is empty)
for var in ECR_REGISTRY AWS_REGION; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set in .env.deploy"
        exit 1
    fi
done
# ─────────────────────────────────────────────────────────────────────────────

# ── Shared variables ──────────────────────────────────────────────────────────
# Note: SSH config for ec2-stock (including .pem key path) lives in ~/.ssh/config
EC2_HOST="ec2-stock"
# Home directory for the EC2 SSH user (ubuntu on Ubuntu, ec2-user on Amazon Linux)
EC2_HOME="/home/ubuntu"
EC2_DAG_PATH="$EC2_HOME/airflow/dags"
EC2_HELM_PATH="$EC2_HOME/airflow/helm"
EC2_BUILD_PATH="$EC2_HOME/dashboard_build"
EC2_DASHBOARD_PATH="$EC2_HOME/dashboard"
FLASK_IMAGE="my-flask-app:latest"
FLASK_POD="my-kuber-pod-flask"
ECR_IMAGE="$ECR_REGISTRY/my-flask-app:latest"
# ─────────────────────────────────────────────────────────────────────────────

# ── Warning/Error Summary ─────────────────────────────────────────────────────
# Runs whenever the script exits — whether it succeeded, was stopped by an error, or called exit 1 directly.
_print_deploy_summary() {
    local exit_code=$?  # save before any other command can overwrite it
    set +e              # turn off 'stop on error' so the summary always finishes printing
    sleep 0.2           # give the tee process a moment to finish writing everything to the log file before we search it
    # Search the log for warnings and errors, removing duplicate lines while keeping them in order
    local summary_lines
    summary_lines=$(grep -E "(WARNING|ERROR|⚠|DeprecationWarning|DEPRECATION:|FutureWarning|UserWarning|✗)" \
        "$DEPLOY_LOGFILE" \
        | grep -v -- "--ignore-not-found" \
        | awk '!seen[$0]++') || true  # || true: if grep finds nothing it exits non-zero — this prevents that from stopping the script
    echo ""
    echo "=================================================================="
    if [ "$exit_code" -eq 0 ]; then
        echo "  DEPLOY COMPLETE"
    else
        echo "  DEPLOY FAILED  (exit code: $exit_code)"
        # Show the exact bash command that triggered the failure — set by the ERR trap in deploy.sh
        if [ -n "${DEPLOY_FAILED_CMD:-}" ]; then
            echo "  Failed command: $DEPLOY_FAILED_CMD"
        fi
    fi
    # Calculate how many seconds the deploy took using bash's built-in $SECONDS timer
    local elapsed=$(( SECONDS - DEPLOY_START ))
    local elapsed_min=$(( elapsed / 60 ))
    local elapsed_sec=$(( elapsed % 60 ))
    printf "  Elapsed time: %dm %02ds\n" "$elapsed_min" "$elapsed_sec"
    echo "  -- Warnings & Errors -------------------------------------------"
    if [ -z "$summary_lines" ]; then
        if [ "$exit_code" -ne 0 ]; then
            # No WARNING/ERROR keywords were found — show the last 15 log lines so there is
            # always a visible trail; this catches failures like SSH exit 255 that print nothing
            echo "  No WARNING/ERROR keywords found. Last 15 log lines:"
            echo ""
            tail -n 15 "$DEPLOY_LOGFILE" | while IFS= read -r line; do
                echo "    > $line"
            done
            echo ""
            echo "  Script exited with errors — check items above and logs for details."
        else
            echo "  (none)"
        fi
    else
        echo ""
        while IFS= read -r line; do
            echo "    > $line"
        done <<< "$summary_lines"
        echo ""
        if [ "$exit_code" -eq 0 ]; then
            echo "  Script ran to completion despite the above — review before closing."
        else
            echo "  Script exited with errors — check items above and logs for details."
        fi
    fi
    echo "=================================================================="
    echo "  Full log: $DEPLOY_LOGFILE"
    echo "=================================================================="
    echo ""
}
# ─────────────────────────────────────────────────────────────────────────────

# ── Background job error helper ───────────────────────────────────────────────
# _wait_bg PID label — waits for a background job to finish, then prints success or failure and exits if it failed.
# WHY this is needed: bash's 'stop on error' setting does not apply to background jobs (&).
# Without this function, a failed background SSH job would disappear silently and the script
# would keep running as if nothing went wrong. This function catches that.
_wait_bg() {
    local pid=$1 label=$2
    if wait "$pid"; then
        echo "✓ $label done"
    else
        echo "✗ $label FAILED"
        exit 1
    fi
}
# ─────────────────────────────────────────────────────────────────────────────
