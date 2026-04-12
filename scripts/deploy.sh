#!/bin/bash
# Deploy updated DAGs and dashboard to EC2 production.
# Usage:
#   ./scripts/deploy.sh                  — full deploy (Docker build, Kafka, MLflow, Flask, Helm, pods)
#   ./scripts/deploy.sh --dags-only      — fast path: only sync DAG files + restart Airflow pods (~5-7 min)
#                                          Use when you only changed .py files in airflow/dags/
#                                          For Dockerfile, values.yaml, Kafka, MLflow, or dashboard changes — run the full deploy.
#   ./scripts/deploy.sh --provision      — run terraform apply first (updates security group IP), then full deploy
#                                          Use when creating a new instance or switching networks
#   ./scripts/deploy.sh --snowflake-setup — bootstrap all Snowflake objects (warehouse, DB, schemas, role, user)
#                                          Run once on a fresh Snowflake account or after a full project teardown.
#                                          Requires SNOWFLAKE_ADMIN_USER, SNOWFLAKE_ADMIN_PASSWORD, SNOWFLAKE_PASSWORD in .env.deploy.
#                                          Safe to re-run — all statements are CREATE IF NOT EXISTS.

# Exit immediately if any command fails, unset variable is used, or pipe fails
set -euo pipefail

# Capture the last failing bash command so _print_deploy_summary can show it.
# ERR fires on every non-zero exit before the EXIT trap runs, so DEPLOY_FAILED_CMD
# always holds the command that triggered the failure.
DEPLOY_FAILED_CMD=""
trap 'DEPLOY_FAILED_CMD="$BASH_COMMAND"' ERR

# Set a flag when the user presses Ctrl+C so the summary can say why it stopped
DEPLOY_INTERRUPTED=false
trap 'DEPLOY_INTERRUPTED=true; exit 130' INT

# ── Log setup ─────────────────────────────────────────────────────────────────
# Save all output to a log file so we can search it for the end-of-run summary
DEPLOY_LOGFILE="/tmp/deploy-last.log"
exec > >(tee "$DEPLOY_LOGFILE") 2>&1  # tee prints to the screen AND saves to the log file; 2>&1 also captures error output
DEPLOY_START=$SECONDS  # save the start time so we can show how long the deploy took
# ─────────────────────────────────────────────────────────────────────────────

# ── Module loading ────────────────────────────────────────────────────────────
# Resolve paths relative to this script so deploy.sh can be called from any directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DEPLOY_DIR="$SCRIPT_DIR/deploy"

# Load helper files in the right order — common.sh must go first since everything else depends on it
source "$DEPLOY_DIR/common.sh"       # shared vars, _wait_bg, _print_deploy_summary, .env.deploy
source "$DEPLOY_DIR/setup.sh"        # step_setup
source "$DEPLOY_DIR/sync.sh"         # step_sync_dags, step_sync_helm_dockerfile, step_sync_manifests_secrets
source "$DEPLOY_DIR/snowflake.sh"    # step_snowflake_setup
source "$DEPLOY_DIR/airflow_image.sh" # step_build_airflow_image
source "$DEPLOY_DIR/kafka.sh"        # step_deploy_kafka
source "$DEPLOY_DIR/mlflow.sh"       # step_deploy_mlflow, step_fix_mlflow_experiment, step_mlflow_portforward
source "$DEPLOY_DIR/flask.sh"        # step_deploy_flask, step_verify_flask
source "$DEPLOY_DIR/airflow_pods.sh" # step_helm_upgrade, step_verify_airflow_image, step_restart_airflow_pods, step_setup_ml_venv

trap '_print_deploy_summary' EXIT  # print the summary whenever the script exits, whether it succeeds, fails, or is interrupted
# ─────────────────────────────────────────────────────────────────────────────

# ── Argument parsing ──────────────────────────────────────────────────────────
# --dags-only: fast path for DAG-only changes — skips Docker build, Kafka, MLflow, Flask, Helm
DAGS_ONLY=false
# --provision: run terraform apply before the deploy to ensure EC2 infrastructure is current
PROVISION=false
# --snowflake-setup: bootstrap Snowflake objects before the rest of the deploy (one-time or after teardown)
SNOWFLAKE_SETUP=false
# --fix-ml-venv: repair a broken ml-venv in the running scheduler pod without a full redeploy (~60s)
FIX_ML_VENV=false
for _arg in "$@"; do
    case "$_arg" in
        --dags-only)       DAGS_ONLY=true ;;
        --provision)       PROVISION=true ;;
        --snowflake-setup) SNOWFLAKE_SETUP=true ;;
        --fix-ml-venv)     FIX_ML_VENV=true ;;
        *) echo "ERROR: Unknown argument: $_arg"; exit 1 ;;
    esac
done
[ "$DAGS_ONLY" = true ]       && echo "--- Mode: --dags-only (skipping Docker build, Kafka, MLflow, Flask, Helm) ---"
[ "$PROVISION" = true ]       && echo "--- Mode: --provision (running Terraform before deploy) ---"
[ "$SNOWFLAKE_SETUP" = true ] && echo "--- Mode: --snowflake-setup (bootstrapping Snowflake objects before deploy) ---"
[ "$FIX_ML_VENV" = true ]     && echo "--- Mode: --fix-ml-venv (repairing ml-venv in running scheduler pod only) ---"

# --fix-ml-venv: skip the full deploy and only rebuild the ml-venv in the running pod
# Useful after a deploy where step_setup_ml_venv printed a WARNING — no pod restart or Docker build needed
if [ "$FIX_ML_VENV" = true ]; then
    _wait_scheduler_exec  # confirm the scheduler container is exec-ready before attempting pip install
    step_setup_ml_venv
    exit 0
fi
# ─────────────────────────────────────────────────────────────────────────────

# ── Rollback procedure ────────────────────────────────────────────────────────
# If the Flask pod fails to start after a deploy, recover using the previous image:
#
#   1. SSH into EC2:
#        ssh ec2-stock
#   2. Re-tag the previous image as latest and re-apply the manifest:
#        docker tag my-flask-app:previous my-flask-app:latest
#        docker tag my-flask-app:previous $ECR_REGISTRY/my-flask-app:latest
#        docker push $ECR_REGISTRY/my-flask-app:latest
#   3. Delete and recreate the Flask pod so K3S pulls the restored image:
#        kubectl delete pod my-kuber-pod-flask -n default --ignore-not-found=true
#        kubectl apply -f ~/dashboard/manifests/pod-flask.yaml
#        kubectl wait pod/my-kuber-pod-flask -n default --for=condition=Ready --timeout=90s
#
# The `my-flask-app:previous` image is tagged at the start of Step 4 on every deploy,
# so it always points to whatever was running before the current deploy started.
# ─────────────────────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════════════════════
# Phase -1: Snowflake bootstrap (only with --snowflake-setup flag)
# Runs first so all Snowflake objects exist before the pipeline DAGs are deployed.
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$SNOWFLAKE_SETUP" = true ]; then
    echo "=== Phase -1: Bootstrapping Snowflake infrastructure ==="
    step_snowflake_setup  # creates warehouse, DB, schemas, role, user via scripts/snowflake_setup.sql
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 0: Terraform provision (only with --provision flag)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$PROVISION" = true ]; then
    echo "=== Phase 0: Provisioning infrastructure via Terraform ==="
    "$SCRIPT_DIR/deploy/terraform.sh" apply  # updates security group IP + no-ops if nothing else changed
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 1: Setup + Sync (always runs)
# ═══════════════════════════════════════════════════════════════════════════════

step_setup  # Steps 1, 1c, 1b: EC2 dirs, kubectl chmod, Python syntax validation

step_sync_dags  # Step 2: rsync airflow/dags/ to EC2

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2: Parallel heavy builds (full deploy only)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$DAGS_ONLY" = false ]; then
    # Sync the Dockerfile and Helm values first — the Airflow image build (below) needs these files before it can start
    step_sync_helm_dockerfile  # Steps 2b, 2b1: rsync Helm values + Dockerfile to EC2

    # Add a timestamp to the image tag so each deploy gets a unique name, forcing K3S to treat it as a new image
    BUILD_TAG="3.1.8-dbt-$(date +%Y%m%d%H%M%S)"
    echo "Build tag: $BUILD_TAG"

    # Run three independent steps at the same time in the background — they don't depend on each other
    # Each step runs in its own background process and gets a copy of all the current variables
    # _wait_bg checks whether each one succeeded — bash's built-in error checking doesn't catch background job failures

    step_build_airflow_image &  # Step 2b2: Docker build + K3S import (~10-30s with cached layers, 2-5 min from scratch)
    AIRFLOW_BUILD_PID=$!

    step_deploy_kafka &  # Steps 2b3-2b4: Kafka manifest rsync + image pull + StatefulSet deploy (~7-10 min)
    KAFKA_PID=$!

    step_deploy_mlflow &  # Steps 2b5-2b6: MLflow manifest rsync + image import + Deployment deploy (~3-5 min)
    MLFLOW_PID=$!
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 3: Secrets (runs while background jobs execute — fast, ~15s)
# ═══════════════════════════════════════════════════════════════════════════════

# Must finish before the Helm upgrade — pods need these secrets to read their environment variables when they start up
step_sync_manifests_secrets  # Steps 2c-2c3: rsync all manifests + apply K8s secrets

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 4: Wait for parallel jobs, then Helm upgrade + Flask deploy (full deploy only)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$DAGS_ONLY" = false ]; then
    # Wait for the Airflow image build first — the Helm upgrade needs the new image to already be loaded into K3S
    _wait_bg $AIRFLOW_BUILD_PID "Airflow Docker build + K3S import (Step 2b2)"
    # Kafka + MLflow must be running before pod restarts (Airflow DAGs connect to both at startup)
    _wait_bg $KAFKA_PID         "Kafka deploy (Steps 2b3-2b4)"
    _wait_bg $MLFLOW_PID        "MLflow deploy (Steps 2b5-2b6)"

    step_helm_upgrade   # Steps 2d + 2e: helm upgrade + apply Airflow service manifest

    step_deploy_flask   # Steps 3-6: dashboard rsync, ECR setup, Flask build/push, Flask pod restart
fi

# ═══════════════════════════════════════════════════════════════════════════════
# Phase 5: Airflow pod restarts (always runs — reloads DAG files in all modes)
# ═══════════════════════════════════════════════════════════════════════════════

if [ "$DAGS_ONLY" = false ]; then
    # Check that K3S didn't automatically delete the Airflow image to free disk space during the ~20 min gap since we built it
    step_verify_airflow_image  # Step 7a
fi

# Restart the scheduler, dag-processor, and triggerer pods — waiting for all three in parallel takes ~360s max instead of 18 min one at a time
step_restart_airflow_pods  # Step 7

# Rebuild the ML Python environment in the scheduler pod — it gets wiped every time the pod restarts
step_setup_ml_venv  # Step 7b

step_fix_mlflow_experiment  # Step 7c: reset MLflow artifact root via sqlite3 (safe to run multiple times)

step_cleanup_dead_pods  # Step 7e: delete Evicted/Error/Unknown pods left over from prior restarts

step_mlflow_portforward  # Step 7d: restart port-forward for MLflow UI on EC2 localhost:5500

if [ "$DAGS_ONLY" = false ]; then
    step_verify_flask  # Step 8: confirm Flask pod is Ready (created in Step 6)
fi

echo ""
echo "=== Done! ==="
echo ""
echo "Verify in browser:"
echo "  Airflow UI:  http://localhost:30080 (requires SSH tunnel — see below)"
echo "  Dashboard:   http://localhost:32147/dashboard/"
echo ""

# Git holds the master copy of all manifests; EC2 has a synced copy for running kubectl commands directly on the server
echo "=== kubectl Workflow ==="
echo "Manifests are version-controlled in Git and synced to EC2:"
echo "  Local (Git):  airflow/manifests/   dashboard/manifests/"
echo "  EC2:          $EC2_HOME/airflow/manifests/   $EC2_HOME/dashboard/manifests/"
echo ""
echo "To apply/update manifests from your Mac:"
echo "  kubectl apply -f airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"
echo "  kubectl apply -f dashboard/manifests/pod-flask.yaml -n default"
echo ""
echo "To apply directly from EC2:"
echo "  ssh ec2-stock"
echo "  kubectl apply -f $EC2_HOME/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"
echo ""

# ACCESS NOTE — these URLs are NOT open to the public by default.
# AWS Security Groups (AWS's firewall) block ALL inbound ports unless you explicitly allow them.
# Right now your EC2 likely only allows SSH from your current IP — ports 30080 and 32147
# are probably blocked, so pasting the URL in a browser from a new location won't work.
#
# You have two options:
#
# Option A — Open the ports in the AWS Security Group (for your current IP only):
#   Go to AWS Console → EC2 → Security Groups → add inbound rules for ports 30080 and 32147,
#   source = your current IP. You'll need to update this every time you change locations,
#   just like you do for SSH. Downside: manual update each time, and the ports are publicly
#   reachable from your IP (anyone at your coffee shop could access them).
#
# Option B — SSH tunnel (recommended / most secure):
#   Run this on your Mac BEFORE opening the browser:
#     ssh -L 6443:localhost:6443 -L 30080:localhost:30080 -L 32147:localhost:32147 -L 5500:localhost:5500 ec2-stock
#   Then access:  http://localhost:30080  and  http://localhost:32147  and  http://localhost:5500 (MLflow)
#   The traffic travels through your existing encrypted SSH connection.
#   The ports stay CLOSED in the Security Group — only you can access them, from anywhere,
#   with no IP updates needed. This is the best-practice approach for personal/dev tools.
