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

# ── Rollback procedure ───────────────────────────────────────────────────────
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

echo "=== Step 1: Ensuring target directories exist on EC2 ==="
ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests $EC2_HOME/airflow/dag-mylogs $EC2_HOME/airflow/docker \
    && chmod 777 $EC2_HOME/airflow/dag-mylogs"  # 777 so Airflow pod (UID 50000) can write to the PVC-backed log dir

echo "=== Step 1c: Ensuring kubectl config is accessible ==="
# K3s kubectl (symlinked to the k3s binary) reads /etc/rancher/k3s/k3s.yaml directly and
# ignores ~/.kube/config. The file is written root-only; chmod 644 so the ubuntu user can read it.
# Runs on every deploy so permissions are restored even if K3s restarts and resets the file.
ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml"

echo "=== Step 1b: Pre-flight validation ==="

# Validate Python syntax in all DAG files (catches typos, indentation errors, missing colons)
# Check exit code directly — py_compile exits non-zero on syntax error (grep on output is unreliable)
echo "Checking Python syntax in DAG files..."
if find airflow/dags -name "*.py" | xargs python3 -m py_compile 2>/dev/null; then
    echo "✓ All DAG files have valid Python syntax"
else
    echo "✗ Syntax error in DAG files. Fix before deploying."
    find airflow/dags -name "*.py" | xargs python3 -m py_compile  # re-run to display the error
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
dag_files = ['dag_stocks', 'dag_weather', 'dag_staleness_check']
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

echo "=== Step 2b1: Syncing Airflow Dockerfile to EC2 ==="
# Sync the Dockerfile so the image can be built on EC2 (image is never pushed to ECR — imported directly into K3S containerd)
rsync -avz --progress airflow/docker/ "$EC2_HOST:$EC2_HOME/airflow/docker/"

echo "=== Step 2b2: Building Airflow+dbt image and importing into K3S ==="
# WHY build on EC2 instead of pushing to ECR:
#   The custom airflow-dbt image only ever needs to exist on this one EC2 instance.
#   ECR would add ~$0.15/month storage cost for no benefit. Instead we build locally
#   and import directly into K3S's containerd image store (separate from Docker's store).
#   pullPolicy: Never in values.yaml tells K3S to use the local image without pulling.
#
# WHY Docker layer cache makes this fast on repeat deploys:
#   docker build reuses cached layers when the Dockerfile and its inputs are unchanged.
#   Only the changed layers are rebuilt — if only DAG files changed, the dbt venv layer
#   (the slow pip install step) is served from cache in seconds.
ssh "$EC2_HOST" "
    echo 'Building airflow-dbt:3.1.8-dbt image...' &&
    docker build -t airflow-dbt:3.1.8-dbt $EC2_HOME/airflow/docker/ &&
    echo 'Importing image into K3S containerd (bypasses Docker image store, which K3S cannot see)...' &&
    docker save airflow-dbt:3.1.8-dbt | sudo k3s ctr images import - &&
    echo 'Verifying image is visible to K3S...' &&
    sudo k3s ctr images list | grep airflow-dbt
"

echo "=== Step 2c2: Syncing dbt profiles secret to EC2 ==="
# profiles.yml is gitignored (contains dbt connection config referencing Snowflake env vars).
# scp copies it from Mac to EC2; kubectl apply creates or updates the dbt-profiles secret idempotently.
# The secret is mounted at /dbt/ in Airflow workers + scheduler (values.yaml extraVolumeMounts).
# BashOperator tasks set DBT_PROFILES_DIR=/dbt so dbt finds profiles.yml at runtime.
if [ -f "$PROJECT_ROOT/profiles.yml" ]; then
    scp "$PROJECT_ROOT/profiles.yml" "$EC2_HOST:$EC2_HOME/profiles.yml"
    ssh "$EC2_HOST" "kubectl create secret generic dbt-profiles \
        --from-file=profiles.yml=$EC2_HOME/profiles.yml \
        -n airflow-my-namespace \
        --dry-run=client -o yaml | kubectl apply -f -"
else
    echo "Note: profiles.yml not found locally — skipping (create it first if dbt is not yet set up)."
fi

echo "=== Step 2d: Applying Helm values to live Airflow release ==="
# Syncing values.yaml to EC2 (step 2b) only copies the file — it does NOT update the running
# Helm release. helm upgrade applies any changes (memory limits, worker count, probes) to the
# live pods. Without this step, values.yaml edits have no effect until a manual helm upgrade.
# --version 1.20.0: pins chart to Airflow 3.x (upgraded from 1.15.0 on 2026-04-06).
# No --reuse-values: use only values.yaml; --reuse-values injects stale 2.x Helm history and fails 3.x schema validation.
# --atomic=false: keeps upgrade applied even if post-upgrade hooks time out (expected on this cluster).
# || echo: hook timeout exits non-zero; suppress so set -e doesn't abort the script (upgrade was still applied).
ssh "$EC2_HOST" "helm upgrade airflow apache-airflow/airflow \
    -n airflow-my-namespace \
    --version 1.20.0 \
    --atomic=false --timeout 2m \
    --force \
    -f $EC2_HELM_PATH/values.yaml" \
  || echo "Note: Helm hook timed out (expected — post-upgrade job takes >2m; upgrade was applied)."

echo "=== Step 2c: Syncing Kubernetes manifests to EC2 ==="
# Reference copies of manifests on EC2 enable direct kubectl apply from EC2 if needed
# (Git remains the source of truth; these are convenience copies for EC2-side operations)
rsync -avz --progress airflow/manifests/ "$EC2_HOST:$EC2_HOME/airflow/manifests/"
rsync -avz --progress dashboard/manifests/ "$EC2_HOST:$EC2_HOME/dashboard/manifests/"

echo "=== Step 2c1: Applying K8s secrets (credentials) ==="
# Apply Snowflake and database credential secrets to both airflow-my-namespace and default namespaces.
# These secrets must exist before Helm upgrade (Step 2d) so pods can reference them in envFrom.
# Secrets are .gitignored (never committed) and stored only locally on EC2.
ssh "$EC2_HOST" "
    if [ -f $EC2_HOME/airflow/manifests/snowflake-secret.yaml ]; then
        echo 'Applying Snowflake credentials to airflow-my-namespace...' &&
        kubectl apply -f $EC2_HOME/airflow/manifests/snowflake-secret.yaml -n airflow-my-namespace &&
        echo 'Applying Snowflake credentials to default namespace (for Flask pod)...' &&
        kubectl apply -f $EC2_HOME/airflow/manifests/snowflake-secret.yaml -n default
    else
        echo 'Note: snowflake-secret.yaml not found — skipping (first deploy before secret created).'
    fi
"

echo "=== Step 2e: Applying Airflow service manifest ==="
# Apply the Airflow UI service so the selector stays in sync with values.yaml changes (e.g. 2.x→3.x component rename)
# Without this, helm upgrade doesn't touch the manually-created NodePort service, so label changes are silently ignored
ssh "$EC2_HOST" "kubectl apply -f $EC2_HOME/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"

echo "=== Step 3: Syncing dashboard build files to EC2 ==="
rsync -avz --progress dashboard/ "$EC2_HOST:$EC2_BUILD_PATH/"

echo "=== Step 4a: Configuring ECR credential helper on EC2 ==="
# amazon-ecr-credential-helper is the AWS-recommended approach for ECR auth:
# it fetches short-lived tokens via the EC2 IAM role transparently on every push/pull,
# so no credentials are ever written to ~/.docker/config.json (fixes the "unencrypted
# credentials" warning that appears when using `docker login`).
ssh "$EC2_HOST" "
    # Install ECR credential helper if not already present
    if ! command -v docker-credential-ecr-login &>/dev/null; then
        sudo apt-get install -y -q amazon-ecr-credential-helper
    fi
    # Install buildx binary if not already present (docker-buildx-plugin only exists in Docker's
    # official apt repo; Ubuntu's docker.io package omits it, so we fetch the binary from GitHub)
    if ! docker buildx version &>/dev/null; then
        BUILDX_VER=\$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
            | python3 -c \"import sys,json; print(json.load(sys.stdin)['tag_name'])\")
        mkdir -p ~/.docker/cli-plugins
        curl -fsSL \"https://github.com/docker/buildx/releases/download/\${BUILDX_VER}/buildx-\${BUILDX_VER}.linux-amd64\" \
            -o ~/.docker/cli-plugins/docker-buildx
        chmod +x ~/.docker/cli-plugins/docker-buildx
        echo \"Installed buildx \${BUILDX_VER}\"
    fi
    # Register the helper for this ECR registry in ~/.docker/config.json (idempotent)
    python3 -c \"
import json, pathlib
p = pathlib.Path.home() / '.docker/config.json'
cfg = json.loads(p.read_text()) if p.exists() else {}
cfg.setdefault('credHelpers', {})['$ECR_REGISTRY'] = 'ecr-login'
p.parent.mkdir(exist_ok=True)
p.write_text(json.dumps(cfg, indent=2))
print('ECR credential helper configured')
    \"
"

echo "=== Step 4: Building Docker image on EC2 and pushing to ECR ==="
# Tag the currently running image as 'previous' before overwriting — enables rollback (see procedure above)
ssh "$EC2_HOST" "docker tag $FLASK_IMAGE my-flask-app:previous 2>/dev/null || true"
# WHY we push to ECR instead of keeping the image local:
#   K3S now uses its default containerd runtime (not the legacy --docker mode).
#   containerd and Docker have separate image stores, so a "docker build" image is NOT
#   visible to K3S. Instead of manually importing the image, we push it to ECR (AWS's
#   private container registry) and let K3S pull it from there — this is the standard
#   production pattern for Kubernetes on AWS.
#
#   Authentication is handled by the ECR credential helper (Step 4a), which uses the
#   EC2 instance's IAM role — no explicit `docker login` needed, no credentials on disk.
# DOCKER_BUILDKIT=1: enables BuildKit, the modern Docker build engine (legacy builder is deprecated)
# ssh runs the quoted string as a command on EC2 (not on my Mac)
# "&&" chains commands: each runs only if the previous one succeeded
ssh "$EC2_HOST" "cd $EC2_BUILD_PATH \
    && DOCKER_BUILDKIT=1 docker build -t $FLASK_IMAGE . \
    && docker tag $FLASK_IMAGE $ECR_IMAGE \
    && docker push $ECR_IMAGE"

echo "=== Step 5: Refreshing ECR pull secret in Kubernetes ==="
# WHY this step is needed:
#   K3S containerd needs credentials to pull from ECR (a private registry).
#   We store those credentials as a Kubernetes "docker-registry" secret named "ecr-credentials".
#   Both Flask pod (in default namespace) and Airflow pods (in airflow-my-namespace) reference
#   this secret via "imagePullSecrets".
#
#   ECR tokens are valid for 12 hours. We refresh the secret on every deploy so it's always
#   current. "--dry-run=client -o yaml | kubectl apply" handles both first-time create and
#   subsequent updates without erroring if the secret already exists.
#
# WHY apply to both namespaces:
#   Kubernetes resolves imagePullSecrets in the SAME namespace as the pod.
#   A secret in the wrong namespace is silently ignored — containerd falls back to a direct
#   (unauthenticated) pull, which fails with ImagePullBackOff. We apply to both:
#   - default namespace (Flask pod)
#   - airflow-my-namespace (Airflow pods: scheduler, webserver, dag-processor, triggerer)

# Create the secret in default namespace (Flask)
ssh "$EC2_HOST" "kubectl create secret docker-registry ecr-credentials \
    -n default \
    --docker-server=$ECR_REGISTRY \
    --docker-username=AWS \
    --docker-password=\$(aws ecr get-login-password --region $AWS_REGION) \
    --dry-run=client -o yaml | kubectl apply -n default -f -"

# Create the secret in airflow-my-namespace (Airflow pods)
ssh "$EC2_HOST" "kubectl create secret docker-registry ecr-credentials \
    -n airflow-my-namespace \
    --docker-server=$ECR_REGISTRY \
    --docker-username=AWS \
    --docker-password=\$(aws ecr get-login-password --region $AWS_REGION) \
    --dry-run=client -o yaml | kubectl apply -n airflow-my-namespace -f -"

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
    echo 'Restarting Triggerer pod...' &&
    kubectl delete pod airflow-triggerer-0 -n airflow-my-namespace --ignore-not-found=true &&
    echo 'Waiting for pods to become Ready (up to 120s each)...' &&
    sleep 10 &&
    kubectl wait pod/airflow-scheduler-0 -n airflow-my-namespace --for=condition=Ready --timeout=120s &&
    kubectl wait pod -l component=dag-processor -n airflow-my-namespace --for=condition=Ready --timeout=120s &&
    kubectl wait pod/airflow-triggerer-0 -n airflow-my-namespace --for=condition=Ready --timeout=120s &&
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
