#!/bin/bash
# Module: mlflow — MLflow manifest sync, image import, deployment, artifact root fix, and port-forward.
# Sourced by deploy.sh; all variables from common.sh are available here.

MLFLOW_IMAGE="ghcr.io/mlflow/mlflow:latest"

step_deploy_mlflow() {
    echo "=== Step 2b5: Syncing MLflow manifests to EC2 ==="
    rsync -avz --progress "$PROJECT_ROOT/airflow/manifests/mlflow/" "$EC2_HOST:$EC2_HOME/airflow/manifests/mlflow/"

    echo "=== Step 2b5a: Importing MLflow image into K3S containerd ==="
    # WHY import instead of letting K3S pull at runtime:
    #   K3S and Docker each have their own separate image store. `imagePullPolicy: Never` tells K3S
    #   to only use images from its own store — never try to pull from the internet.
    #   Same pattern used for the airflow-dbt image (Step 2b2).
    #   We pull the image using Docker (which caches layers), then pipe it directly into K3S's image store.
    #   On repeat deploys this is fast because Docker only downloads what's changed.
    ssh "$EC2_HOST" "
        echo 'Pruning old MLflow images from K3S containerd to free ephemeral storage...' &&
        sudo k3s ctr images ls | grep 'mlflow' | awk '{print \$1}' | xargs -r sudo k3s ctr images rm 2>/dev/null || true &&
        echo 'Pruning dangling Docker images to free disk space...' &&
        docker image prune -f || true &&
        echo 'Pulling MLflow image via Docker...' &&
        docker pull $MLFLOW_IMAGE &&
        echo 'Importing into K3S containerd...' &&
        docker save $MLFLOW_IMAGE | sudo k3s ctr images import - &&
        echo 'Verifying image is visible to K3S...' &&
        sudo k3s ctr images list | grep mlflow
    "

    echo "=== Step 2b6: Deploying MLflow to K3s (safe to run multiple times) ==="
    # Print node taints and pressure conditions before deploy — catches scheduling blockers early
    ssh "$EC2_HOST" "
        echo '--- Node taints and pressure conditions pre-MLflow-rollout ---'
        kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints'
        kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: {range .status.conditions[*]}{.type}={.status}  {end}{\"\n\"}{end}'
    "
    ssh "$EC2_HOST" "
        # Make sure the MLflow data folder exists on the EC2 host — this is where the hostPath volume points
        mkdir -p /home/ubuntu/mlflow-data/artifacts

        # Apply in this order: storage → storage claim → the app itself → the network service that exposes it
        kubectl apply -f $EC2_HOME/airflow/manifests/mlflow/pv-mlflow.yaml \
        && kubectl apply -f $EC2_HOME/airflow/manifests/mlflow/pvc-mlflow.yaml -n airflow-my-namespace \
        && kubectl apply -f $EC2_HOME/airflow/manifests/mlflow/deployment-mlflow.yaml -n airflow-my-namespace \
        && kubectl apply -f $EC2_HOME/airflow/manifests/mlflow/service-mlflow.yaml -n airflow-my-namespace \
        && echo 'MLflow manifests applied.'

        # Kubernetes's Recreate strategy shuts down the old pod before starting the new one —
        # this avoids two pods trying to write to the same storage at the same time
        kubectl rollout status deployment/mlflow -n airflow-my-namespace --timeout=180s \
        || {
            echo 'ERROR: MLflow rollout timed out. Diagnosing...'
            echo '--- MLflow pod status ---'
            kubectl get pods -n airflow-my-namespace -l app=mlflow
            echo '--- MLflow pod describe (last 30 lines) ---'
            kubectl describe pod -n airflow-my-namespace -l app=mlflow | tail -30
            echo '--- MLflow pod logs (last 30 lines) ---'
            kubectl logs -n airflow-my-namespace -l app=mlflow --tail=30 2>/dev/null \
                || echo '(no logs — pod may not have started)'
            exit 1
        }
    "
}

step_fix_mlflow_experiment() {
    echo "=== Step 7c: Resetting MLflow experiment artifact root ==="
    # Safe to run multiple times — skips the fix if the experiment already has the correct artifact location.
    # We update the experiment row directly in SQLite instead of deleting and recreating it. That's because
    # MLflow marks deleted experiments as deleted but keeps the row in the database — trying to create a new
    # experiment with the same name would fail with a 'name already exists' error. Updating in-place sidesteps that entirely.

    # Wait for MLflow to be fully up before connecting to it. The health check can take up to ~40s to pass
    # (10s startup delay + 6 checks × 5s each), so without this wait the scheduler pod would get a
    # 'connection refused' error on port 5500.
    ssh "$EC2_HOST" "kubectl rollout status deployment/mlflow -n airflow-my-namespace --timeout=120s"

    # Fix the artifact storage path directly in the SQLite database inside the MLflow pod.
    # Skipping the scheduler-pod Python diagnostic check that was here previously — importing mlflow inside
    # the scheduler container spiked 500–800 MB and OOM-killed it (exit 137). The sqlite3 command below
    # already prints pre-fix state and skips if already correct, so the diagnostic check was redundant.
    # MLflow doesn't truly delete experiments — it just marks them deleted. So trying to create a new one
    # with the same name fails with a 'name already taken' error.
    # Updating the row directly avoids that problem: we set the correct artifact path and mark it active again in one step.
    ssh "$EC2_HOST" "kubectl exec -n airflow-my-namespace deployment/mlflow -- python3 -c \"
import sqlite3, time, sys
db = sqlite3.connect('/mlflow-data/mlflow.db')
# Check current state first — skip if already correct to stay safe to run multiple times
row = db.execute(\\\"SELECT experiment_id, artifact_location, lifecycle_stage FROM experiments WHERE name='anomaly_detection'\\\").fetchone()
if row is None:
    print('No anomaly_detection experiment found — nothing to fix')
    db.close(); sys.exit(0)
exp_id, art_loc, stage = row
if art_loc == 'mlflow-artifacts:/' and stage == 'active':
    print(f'Experiment {exp_id} already has correct root and is active — skipping sqlite3 fix')
    db.close(); sys.exit(0)
# Update artifact_location + restore lifecycle_stage + bump last_update_time
updated = db.execute(
    \\\"UPDATE experiments SET artifact_location='mlflow-artifacts:/', lifecycle_stage='active', last_update_time=? WHERE name='anomaly_detection'\\\",
    (int(time.time() * 1000),)
).rowcount
db.commit(); db.close()
print(f'sqlite3 UPDATE: fixed {updated} row(s) — id={exp_id}, was root={art_loc}, stage={stage}')
\""

    # MLFLOW_TRACKING_URI is set via AIRFLOW_VAR_MLFLOW_TRACKING_URI in values.yaml — no kubectl exec needed.
    # kubectl exec airflow variables set OOM-kills (exit 137) the scheduler on Airflow 3.x because importing
    # the full provider stack spikes memory past the container's 2Gi limit.
}

step_cleanup_dead_pods() {
    echo "=== Step 7e: Cleaning up dead/evicted pods in airflow-my-namespace ==="
    # Delete Failed and Unknown pods left over from evictions, OOM kills, or restarts — keeps the namespace clean
    # --ignore-not-found=true makes this safe to run multiple times (no error if nothing to delete)
    ssh "$EC2_HOST" "
        kubectl delete pods -n airflow-my-namespace \
            --field-selector=status.phase=Failed --ignore-not-found=true
        kubectl delete pods -n airflow-my-namespace \
            --field-selector=status.phase=Unknown --ignore-not-found=true
        echo '[cleanup] dead pod cleanup complete'
    "
}

step_restart_mlflow_pod() {
    echo "=== MLflow pod restart (recovery) ==="
    # Deletes the running MLflow pod so Kubernetes recreates it fresh — use when the MLflow UI
    # is crashing or returning stale data after a failed experiment fix or storage eviction.
    # Not called in the default deploy flow; invoke manually when the UI is broken.
    ssh "$EC2_HOST" "
        kubectl delete pod -n airflow-my-namespace -l app=mlflow --ignore-not-found=true &&
        echo 'Waiting for MLflow pod to be recreated and ready...' &&
        kubectl rollout status deployment/mlflow -n airflow-my-namespace --timeout=120s &&
        echo 'MLflow pod restarted successfully.'
    "
    # Re-establish the port-forward after restart — the old forward dies when the pod is deleted
    step_mlflow_portforward
}

step_mlflow_portforward() {
    echo "=== Step 7d: Starting kubectl port-forward for MLflow UI (EC2 localhost:5500) ==="
    # Non-fatal: port-forward is UI convenience only — deploy is complete regardless of this step.
    # SSH exit 255 (connection timeout) after a long deploy is the known failure mode here; we
    # degrade gracefully with a WARNING rather than killing an otherwise-successful deploy.

    # Consolidate kill/start/verify into one SSH session — avoids multiple reconnect attempts
    # after a long deploy may have let the ControlMaster socket or connection expire.
    _pf_exit=0
    ssh -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 \
        "$EC2_HOST" bash << 'REMOTE' || _pf_exit=$?
# Kill any stale port-forward on 5500 to avoid "address already in use" on redeploy.
# || true suppresses pkill's own non-zero exit when no matching process is found.
pkill -f 'kubectl port-forward svc/mlflow' || true
# Release port 5500 by PID in case a prior forward was left under a different name.
# >/dev/null 2>&1 suppresses the killed PID that fuser prints to stdout (no trailing newline),
# which would otherwise bleed into the next deploy-log line and corrupt the summary grep.
fuser -k 5500/tcp >/dev/null 2>&1 || true

sleep 1

# Forward port 5500 on EC2's localhost to the MLflow service inside the cluster,
# so your SSH tunnel (-L 5500:localhost:5500) can reach it from your Mac.
# Kubernetes ClusterIP services are only reachable from inside the cluster — this port-forward makes MLflow reachable from the EC2 host's localhost.
# </dev/null closes kubectl's stdin — without it, kubectl keeps the SSH connection's stdin open,
# which prevents the SSH session from ending cleanly after backgrounding and eventually causes the SSH client to time out with exit 255.
nohup kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 \
    --address=127.0.0.1 </dev/null > /tmp/mlflow-portforward.log 2>&1 &

# Retry up to 3 times (3s apart) — kubectl prints "Forwarding from" only once it has
# successfully bound the port and confirmed the endpoint is reachable.
_pf_ok=0
for _attempt in 1 2 3; do
    sleep 3
    # "Forwarding from" is kubectl's own success signal — more reliable than pgrep,
    # which only checks process existence, not whether the forward is actually working.
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
REMOTE
    # The outer check catches SSH connection failure (exit 255) on the local side.
    if [[ $_pf_exit -ne 0 ]]; then
        echo "WARNING: MLflow port-forward setup failed (SSH exit $_pf_exit) — tunnel may need manual restart via: ssh ec2-stock 'nohup kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 --address=127.0.0.1 </dev/null > /tmp/mlflow-portforward.log 2>&1 &'"
        return 0
    fi
}
