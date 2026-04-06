#!/bin/bash
# Deploy updated DAGs and dashboard to EC2 production

# Exit immediately if any command fails, unset variable is used, or pipe fails
set -euo pipefail

# ── Load deploy secrets from .env.deploy ─────────────────────────────────────
# .env.deploy is gitignored and contains real AWS values (ECR registry, region).
# See .env.deploy.example for the template. This keeps AWS account IDs out of git.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_DEPLOY="$PROJECT_ROOT/.env.deploy"

if [ ! -f "$ENV_DEPLOY" ]; then
    echo "ERROR: $ENV_DEPLOY not found."
    echo "Copy .env.deploy.example to .env.deploy and fill in your AWS values."
    echo "  cp .env.deploy.example .env.deploy"
    exit 1
fi

# shellcheck source=../.env.deploy
source "$ENV_DEPLOY"

# Validate required variables are set (catches empty .env.deploy)
for var in ECR_REGISTRY AWS_REGION; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set in .env.deploy"
        exit 1
    fi
done
# ─────────────────────────────────────────────────────────────────────────────

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

echo "=== Step 1: Ensuring target directories exist on EC2 ==="
ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests $EC2_HOME/airflow/dag-mylogs \
    && chmod 777 $EC2_HOME/airflow/dag-mylogs"  # 777 so Airflow pod (UID 50000) can write to the PVC-backed log dir

echo "=== Step 1b: Pre-flight validation ==="

# Validate Python syntax in all DAG files (catches typos, indentation errors, missing colons)
echo "Checking Python syntax in DAG files..."
if ! python3 -m py_compile airflow/dags/*.py 2>&1 | grep -q "error"; then
    echo "✓ All DAG files have valid Python syntax"
else
    echo "✗ Syntax error in DAG files. Fix before deploying."
    python3 -m py_compile airflow/dags/*.py
    exit 1
fi

# Validate that all DAG imports work (catches missing modules, missing secrets, etc.)
echo "Validating module imports..."
cd airflow/dags
python3 << 'VALIDATION_EOF'
import sys
sys.path.insert(0, '.')  # Simulate /opt/airflow/dags in the pod

# Skip import check if airflow is not installed locally (only available inside the pod)
try:
    import airflow
except ImportError:
    print("⚠ airflow not installed locally — skipping import validation (syntax already verified above)")
    sys.exit(0)

# Try importing all DAG files
dag_files = ['dag_stocks', 'dag_weather']
for dag_file in dag_files:
    try:
        __import__(dag_file)
        print(f"✓ {dag_file} imports successfully")
    except ImportError as e:
        print(f"✗ Import error in {dag_file}: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Runtime error in {dag_file}: {e}")
        sys.exit(1)

print("✓ All DAG files import successfully")
VALIDATION_EOF
cd ../..

echo ""

echo "=== Step 2: Syncing DAG files to EC2 ==="
# rsync <- Unix/Mac/Linux command to transfer data/files.
# Compares source/destination so it only transfers what was changed.
# -a: archive mode (preserves permissions and timestamps)
# -v: verbose (shows which files were transferred)
# -z: compress data in transit
# --progress: shows per-file progress bar and transfer speed
# Trailing "/" on source means "sync contents of folder", not the folder itself
# Note: rsync does NOT read .gitignore, so api_key.py, db_config.py, constants.py are included (intentional)
rsync -avz --progress airflow/dags/ "$EC2_HOST:$EC2_DAG_PATH/"

echo "=== Step 2b: Syncing Helm values to EC2 ==="
rsync -avz --progress airflow/helm/values.yaml "$EC2_HOST:$EC2_HELM_PATH/"

echo "=== Step 2d: Applying Helm values to live Airflow release ==="
# Syncing values.yaml to EC2 (step 2b) only copies the file — it does NOT update the running
# Helm release. helm upgrade applies any changes (memory limits, worker count, probes) to the
# live pods. Without this step, values.yaml edits have no effect until a manual helm upgrade.
# --version 1.15.0: pins chart version (same as bootstrap_ec2.sh) to prevent accidental upgrades.
# --reuse-values: keeps any runtime-set values not present in our file (e.g. generated secrets).
ssh "$EC2_HOST" "helm upgrade airflow apache-airflow/airflow \
    -n airflow-my-namespace \
    --version 1.15.0 \
    --reuse-values \
    -f $EC2_HELM_PATH/values.yaml"

echo "=== Step 2c: Syncing Kubernetes manifests to EC2 ==="
# Reference copies of manifests on EC2 enable direct kubectl apply from EC2 if needed
# (Git remains the source of truth; these are convenience copies for EC2-side operations)
rsync -avz --progress airflow/manifests/ "$EC2_HOST:$EC2_HOME/airflow/manifests/"
rsync -avz --progress dashboard/manifests/ "$EC2_HOST:$EC2_HOME/dashboard/manifests/"

echo "=== Step 3: Syncing dashboard build files to EC2 ==="
rsync -avz --progress dashboard/ "$EC2_HOST:$EC2_BUILD_PATH/"

echo "=== Step 4: Building Docker image on EC2 and pushing to ECR ==="
# WHY we push to ECR instead of keeping the image local:
#   K3S now uses its default containerd runtime (not the legacy --docker mode).
#   containerd and Docker have separate image stores, so a "docker build" image is NOT
#   visible to K3S. Instead of manually importing the image, we push it to ECR (AWS's
#   private container registry) and let K3S pull it from there — this is the standard
#   production pattern for Kubernetes on AWS.
#
#   Authentication uses the EC2 instance's IAM role (no passwords stored anywhere):
#   "aws ecr get-login-password" fetches a temporary token via the instance metadata service,
#   then pipes it into "docker login" so Docker can push to your private ECR repo.
# ssh runs the quoted string as a command on EC2 (not on my Mac)
# "&&" chains commands: each runs only if the previous one succeeded
ssh "$EC2_HOST" "aws ecr get-login-password --region $AWS_REGION \
    | docker login --username AWS --password-stdin $ECR_REGISTRY \
    && cd $EC2_BUILD_PATH && docker build -t $FLASK_IMAGE . \
    && docker tag $FLASK_IMAGE $ECR_IMAGE \
    && docker push $ECR_IMAGE"

echo "=== Step 5: Refreshing ECR pull secret in Kubernetes ==="
# WHY this step is needed:
#   K3S containerd needs credentials to pull from ECR (a private registry).
#   We store those credentials as a Kubernetes "docker-registry" secret named "ecr-credentials".
#   The pod manifest (pod-flask.yaml) references this secret via "imagePullSecrets".
#
#   ECR tokens are valid for 12 hours. We refresh the secret on every deploy so it's always
#   current. "--dry-run=client -o yaml | kubectl apply" handles both first-time create and
#   subsequent updates without erroring if the secret already exists.
#
# WHY -n default on both sides:
#   The kubectl context on this EC2 instance defaults to airflow-my-namespace (where Airflow
#   lives). Without an explicit namespace, the secret would be created there instead of in the
#   default namespace where the Flask pod runs. Kubernetes resolves imagePullSecrets in the
#   SAME namespace as the pod, so a secret in the wrong namespace is silently ignored —
#   containerd falls back to a direct (unauthenticated) pull, which fails with ImagePullBackOff
#   unless the image happens to already be cached on the node.
ssh "$EC2_HOST" "kubectl create secret docker-registry ecr-credentials \
    -n default \
    --docker-server=$ECR_REGISTRY \
    --docker-username=AWS \
    --docker-password=\$(aws ecr get-login-password --region $AWS_REGION) \
    --dry-run=client -o yaml | kubectl apply -n default -f -"

echo "=== Step 6: Restarting Flask pod to pick up the new image ==="
# WHY delete+recreate instead of just "restart":
#
#   Kubernetes has two common ways to run containers:
#
#   1. Plain Pod (what we have — see dashboard/manifests/pod-flask.yaml, line: "kind: Pod")
#      A single container definition with no supervisor watching over it.
#      If it crashes, it stays dead. No built-in update mechanism.
#      To "update" it: you must manually delete it and apply the manifest again.
#
#   2. Deployment (best practice for production services)
#      A controller that wraps pods and manages their lifecycle.
#      Supports "kubectl rollout restart deployment/name" which does a zero-downtime swap:
#      starts the new pod first, waits for it to be healthy, THEN kills the old one.
#      Also auto-restarts crashed pods.
#
#   How do you know this is a plain Pod? Open dashboard/manifests/pod-flask.yaml and look
#   at the top — "kind: Pod" means plain Pod. "kind: Deployment" would mean a Deployment.
#
#   For a personal/learning project a plain Pod is fine. For production services handling
#   real traffic, use a Deployment so you get zero-downtime restarts and auto-recovery.
#
# "--ignore-not-found" prevents an error if the pod doesn't exist yet (e.g. first deploy)
# "-n default" is required: kubectl context default namespace is airflow-my-namespace on this cluster
#
# WHY kubectl wait --for=delete before kubectl apply:
#   Plain Pods are terminated asynchronously — "kubectl delete" tells the API server to start
#   graceful termination, but returns immediately while the pod status is still "Terminating".
#   If kubectl apply runs at that point, it sees the pod object still in etcd (spec unchanged)
#   and prints "unchanged" without creating a new pod. By the time kubectl wait --for=condition=Ready
#   runs in Step 7, the pod has finished terminating and is completely gone. The "|| true" lets
#   the script continue if the pod was already absent (first deploy or already fully deleted).
ssh "$EC2_HOST" "kubectl delete pod $FLASK_POD -n default --ignore-not-found=true && kubectl wait --for=delete pod/$FLASK_POD -n default --timeout=30s 2>/dev/null || true"

# A "manifest" is a YAML file that declares a Kubernetes object (pod, service, volume, etc.)
# YAML is a human-readable config format — like JSON but without all the brackets and quotes.
# "kubectl apply -f file.yaml" means: "make the cluster match what's described in this file"
# This is called declarative configuration — you describe WHAT you want, not HOW to build it.
# Best practice: define all infrastructure in YAML files committed to git, so your entire
# cluster setup is version-controlled, reviewable, and reproducible from scratch.
# pod-flask.yaml in git contains ${ECR_REGISTRY} as a placeholder for the ECR image URI.
# We substitute the real value from .env.deploy before applying, so the AWS account ID
# stays out of version control. envsubst replaces ${ECR_REGISTRY} with the actual URI.
# service-flask.yaml has no secrets, so it's applied as-is.
# envsubst is in /opt/homebrew/bin on Apple Silicon Macs; fall back to sed if not found
if command -v envsubst &>/dev/null; then
    ECR_REGISTRY="$ECR_REGISTRY" envsubst '${ECR_REGISTRY}' < dashboard/manifests/pod-flask.yaml > /tmp/pod-flask-rendered.yaml
else
    sed "s|\${ECR_REGISTRY}|$ECR_REGISTRY|g" dashboard/manifests/pod-flask.yaml > /tmp/pod-flask-rendered.yaml
fi
rsync -avz /tmp/pod-flask-rendered.yaml "$EC2_HOST:/tmp/pod-flask.yaml"
rsync -avz dashboard/manifests/service-flask.yaml "$EC2_HOST:/tmp/"
ssh "$EC2_HOST" "kubectl apply -f /tmp/service-flask.yaml && kubectl apply -f /tmp/pod-flask.yaml"

echo "=== Step 7: Restarting Airflow pods to prevent stale DAG cache ==="
# WHY this step is needed:
#   When new DAG files are synced to EC2, K8s pods may retain a stale filesystem cache
#   of /opt/airflow/dags/. The DAG Processor pod in particular can see old directory
#   listings even though the files are updated on disk. This causes Airflow to mark newly
#   deployed DAGs as is_stale=True and remove them from the UI after ~90 seconds.
#
#   Restarting both Scheduler and Processor pods forces K8s to remount the volume with a
#   fresh filesystem view. This is the proven fix from the 2026-03-31 staleness incident.
ssh "$EC2_HOST" "
    echo 'Restarting Scheduler pod...' &&
    kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace --ignore-not-found=true &&
    echo 'Restarting DAG Processor pod(s)...' &&
    kubectl delete pod -l component=dag-processor -n airflow-my-namespace --ignore-not-found=true &&
    echo 'Waiting 60s for pods to restart...' &&
    sleep 60 &&
    echo 'Verifying DAGs are visible...' &&
    kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list
" || {
    echo ""
    echo "WARNING: Airflow pod restart or DAG verification failed. Check manually."
    ssh "$EC2_HOST" "kubectl get pods -n airflow-my-namespace"
}

echo "=== Step 8: Verifying deployment ==="
# Wait up to 90s for the Flask pod to reach Running/Ready, then print all pod statuses.
# WHY kubectl wait instead of kubectl get:
#   The pod is created in Step 6 but starts Pending while K3S pulls the ECR image (~15-60s).
#   Checking immediately (as we did before) always shows Pending, which gives no useful signal.
#   "kubectl wait --for=condition=Ready" blocks here until the pod is healthy, then we print
#   the final status. On timeout it falls through to the || block and prints describe output
#   so you can read the Events section and see exactly what went wrong.
ssh "$EC2_HOST" "
    echo 'Waiting for $FLASK_POD to be ready (up to 90s)...' &&
    kubectl wait pod/$FLASK_POD -n default --for=condition=Ready --timeout=90s &&
    echo '' &&
    echo 'Pod is Running. All pods:' &&
    kubectl get pods -n default
" || {
    echo ""
    echo "WARNING: Flask pod did not become Ready within 90s. Current state:"
    ssh "$EC2_HOST" "kubectl get pods -n default && echo '' && kubectl describe pod $FLASK_POD -n default | tail -20"
}

echo ""
echo "=== Done! ==="
echo ""
echo "Verify in browser:"
echo "  Airflow UI:  http://localhost:30080 (requires SSH tunnel — see below)"
echo "  Dashboard:   http://localhost:32147/dashboard/"
echo ""

# kubectl workflow — Git is source of truth, manifests synced to EC2 for reference/convenience
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
#     ssh -L 6443:localhost:6443 -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock
#   Then access:  http://localhost:30080  and  http://localhost:32147
#   The traffic travels through your existing encrypted SSH connection.
#   The ports stay CLOSED in the Security Group — only you can access them, from anywhere,
#   with no IP updates needed. This is the best-practice approach for personal/dev tools.
