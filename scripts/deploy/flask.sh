#!/bin/bash
# Module: flask — Dashboard rsync, ECR credential setup, Flask Docker build/push, pod lifecycle, readiness check.
# Sourced by deploy.sh; all variables from common.sh are available here.

step_deploy_flask() {
    echo "=== Step 3: Syncing dashboard build files to EC2 ==="
    rsync -avz --progress "$PROJECT_ROOT/dashboard/" "$EC2_HOST:$EC2_BUILD_PATH/"

    echo "=== Step 4a: Configuring ECR credential helper on EC2 ==="
    # amazon-ecr-credential-helper is the recommended way to authenticate with ECR. It automatically gets
    # short-lived access tokens using the EC2 machine's IAM role, so Docker never has to store AWS credentials
    # on disk (which avoids the "unencrypted credentials" warning you'd see with `docker login`).
    ssh "$EC2_HOST" "
        # Install ECR credential helper if not already present
        if ! command -v docker-credential-ecr-login &>/dev/null; then
            sudo apt-get install -y -q amazon-ecr-credential-helper
        fi
        # Install docker buildx if it's not already there. The version in Ubuntu's default apt repo doesn't
        # include it, so we download the binary directly from GitHub.
        if ! docker buildx version &>/dev/null; then
            BUILDX_VER=\$(curl -fsSL https://api.github.com/repos/docker/buildx/releases/latest \
                | python3 -c \"import sys,json; print(json.load(sys.stdin)['tag_name'])\")
            mkdir -p ~/.docker/cli-plugins
            curl -fsSL \"https://github.com/docker/buildx/releases/download/\${BUILDX_VER}/buildx-\${BUILDX_VER}.linux-amd64\" \
                -o ~/.docker/cli-plugins/docker-buildx
            chmod +x ~/.docker/cli-plugins/docker-buildx
            echo \"Installed buildx \${BUILDX_VER}\"
        fi
        # Tell Docker to use the ECR credential helper for this registry — updates ~/.docker/config.json, and is safe to run multiple times
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
    # Tag the currently running image as 'previous' before overwriting — enables rollback (see procedure in deploy.sh)
    ssh "$EC2_HOST" "docker tag $FLASK_IMAGE my-flask-app:previous 2>/dev/null || true"
    # WHY we push to ECR instead of keeping the image local:
    #   K3S now uses its default containerd runtime (not the legacy --docker mode).
    #   K3S and Docker keep images in separate stores, so a `docker build` image is NOT visible to K3S.
    #   Instead of manually importing it, we push to ECR (AWS's private image registry) and let K3S pull
    #   from there — the standard Kubernetes pattern on AWS.
    #
    #   Authentication is handled by the ECR credential helper from Step 4a, which uses the EC2 machine's
    #   IAM role automatically — no `docker login` needed, no credentials stored on disk.
    # DOCKER_BUILDKIT=1 turns on BuildKit, Docker's modern build engine (the old builder is deprecated)
    ssh "$EC2_HOST" "cd $EC2_BUILD_PATH \
        && DOCKER_BUILDKIT=1 docker build -t $FLASK_IMAGE . \
        && docker tag $FLASK_IMAGE $ECR_IMAGE \
        && docker push $ECR_IMAGE"

    echo "=== Step 5: Refreshing ECR pull secret in Kubernetes ==="
    # WHY this step is needed:
    #   K3S needs credentials to pull images from ECR, which is a private registry.
    #   We store those credentials as a Kubernetes "docker-registry" secret named "ecr-credentials".
    #   Both the Flask pod and Airflow pods reference this secret via `imagePullSecrets`.
    #
    #   ECR tokens expire after 12 hours, so we refresh the secret on every deploy to keep it valid.
    #   `--dry-run=client -o yaml | kubectl apply` creates the secret if it's new, or updates it if it
    #   already exists — without throwing an error either way.
    #
    # WHY apply to both namespaces:
    #   Kubernetes only looks for pull secrets in the same namespace as the pod.
    #   A secret in the wrong namespace is silently ignored — the pod then tries to pull without
    #   credentials and fails with ImagePullBackOff. We apply to both:
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
    #      A single container with no supervisor watching over it. If it crashes, it stays dead.
    #      There's no built-in "restart" command — you have to delete the pod and re-apply the manifest.
    #
    #   2. Deployment (best practice for production)
    #      A controller that manages the pod's lifecycle. Supports `kubectl rollout restart`, which
    #      starts the new pod first, waits for it to be healthy, then kills the old one (zero downtime).
    #      Also auto-restarts the pod if it crashes.
    #
    #   How do you know which one this is? Open dashboard/manifests/pod-flask.yaml — "kind: Pod" means
    #   it's a plain Pod. "kind: Deployment" would mean a Deployment. For a personal project a plain
    #   Pod is fine; for production use a Deployment.
    #
    # "--ignore-not-found" prevents an error if the pod doesn't exist yet (e.g. first deploy)
    # "-n default" is required: the kubectl context default namespace is airflow-my-namespace on this cluster
    #
    # WHY kubectl wait --for=delete before kubectl apply:
    #   "kubectl delete" kicks off graceful termination but returns immediately — the pod is still visible
    #   in Kubernetes while it shuts down ("Terminating" status). If we run kubectl apply at that moment,
    #   Kubernetes sees the pod object still exists and prints "unchanged" without creating a new one.
    #   We wait for the old pod to fully disappear before applying. The "|| true" lets the script
    #   continue if the pod was already gone (first deploy or already fully deleted).
    ssh "$EC2_HOST" "kubectl delete pod $FLASK_POD -n default --ignore-not-found=true && kubectl wait --for=delete pod/$FLASK_POD -n default --timeout=30s 2>/dev/null || true"

    # pod-flask.yaml in git contains ${ECR_REGISTRY} as a placeholder for the ECR image URI.
    # We substitute the real value from .env.deploy before applying, so the AWS account ID
    # stays out of version control. envsubst replaces ${ECR_REGISTRY} with the actual URI.
    # service-flask.yaml has no secrets, so it's applied as-is.
    # envsubst lives at /opt/homebrew/bin on Apple Silicon Macs; we fall back to sed if it's not available
    if command -v envsubst &>/dev/null; then
        ECR_REGISTRY="$ECR_REGISTRY" envsubst '${ECR_REGISTRY}' < "$PROJECT_ROOT/dashboard/manifests/pod-flask.yaml" > /tmp/pod-flask-rendered.yaml
    else
        sed "s|\${ECR_REGISTRY}|$ECR_REGISTRY|g" "$PROJECT_ROOT/dashboard/manifests/pod-flask.yaml" > /tmp/pod-flask-rendered.yaml
    fi
    rsync -avz /tmp/pod-flask-rendered.yaml "$EC2_HOST:/tmp/pod-flask.yaml"
    rsync -avz "$PROJECT_ROOT/dashboard/manifests/service-flask.yaml" "$EC2_HOST:/tmp/"
    ssh "$EC2_HOST" "kubectl apply -f /tmp/service-flask.yaml && kubectl apply -f /tmp/pod-flask.yaml"
}

step_verify_flask() {
    echo "=== Step 8: Verifying deployment ==="
    # Wait up to 90s for the Flask pod to become Ready, then print all pod statuses.
    # WHY kubectl wait instead of kubectl get:
    #   The pod starts in a Pending state while K3S downloads the image from ECR (~15-60s).
    #   Checking immediately always shows Pending, which tells you nothing useful.
    #   `kubectl wait --for=condition=Ready` pauses here until the pod is healthy, then we print the result.
    #   If it times out, the || block runs and prints describe output — the Events section there will tell
    #   you exactly what went wrong.
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
}
