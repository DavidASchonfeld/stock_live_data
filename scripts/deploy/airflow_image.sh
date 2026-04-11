#!/bin/bash
# Module: airflow_image — Docker build of the custom Airflow+dbt image and K3S import.
# Sourced by deploy.sh; BUILD_TAG must be set in deploy.sh before calling step_build_airflow_image.

step_build_airflow_image() {
    echo "=== Step 2b2: Building Airflow+dbt image and importing into K3S ==="
    # WHY build on EC2 instead of pushing to ECR:
    #   The custom airflow-dbt image only ever needs to exist on this one EC2 instance.
    #   ECR would add ~$0.15/month storage cost for no benefit. Instead we build locally
    #   and import directly into K3S's own image store (K3S and Docker each keep their own separate copy of images).
    #   `pullPolicy: Never` in values.yaml tells K3S to only use the locally imported image — never try to pull it from the internet.
    #
    # WHY Docker layer cache is safe here (--no-cache is NOT used):
    #   Docker's build cache skips any steps in the Dockerfile that haven't changed.
    #   The K3S side is handled separately: the BUILD_TAG always has a fresh timestamp, so K3S
    #   always treats it as a new image and imports it fresh. The image cleanup steps below also
    #   remove any leftover old images from K3S. --no-cache was targeting Docker's cache (which
    #   was fine to keep), not K3S's cache — so it added 2-5 min to every deploy with no benefit.
    #
    # WHY Dockerfile changes ARE picked up by the cache:
    #   If you change the Dockerfile (like updating a pip package version), Docker detects it and
    #   rebuilds from that point forward. Everything before the change is reused.
    #   DAG files are not included in the Docker build — they're copied separately via rsync — so
    #   editing a DAG file does not trigger a Docker rebuild (which is correct: the image itself doesn't need to change).
    #
    # By using a new tag each time, K3S has never seen it before and always loads the image fresh.
    # (If you re-import under the same tag name, K3S can silently reuse the old cached version
    # even after you've deleted and re-imported it.)
    echo "Build tag: $BUILD_TAG"
    ssh "$EC2_HOST" "
        echo 'Building airflow-dbt:$BUILD_TAG image...' &&
        docker build -t airflow-dbt:$BUILD_TAG $EC2_HOME/airflow/docker/ &&
        echo 'Purging ALL existing airflow-dbt images from K3S containerd (prevents stale snapshot reuse)...' &&
        sudo k3s ctr images ls | grep 'airflow-dbt' | awk '{print \$1}' | xargs -r sudo k3s ctr images rm 2>/dev/null || true &&
        echo 'Running K3S containerd content GC to free orphaned blobs from disk...' &&
        sudo k3s ctr content gc || true &&
        echo 'Pruning old airflow-dbt Docker images from previous builds to free disk space...' &&
        docker images --format '{{.Repository}}:{{.Tag}}' | grep 'airflow-dbt' | grep -v '$BUILD_TAG' | xargs -r docker rmi 2>/dev/null || true &&
        echo 'Pruning dangling Docker images to free disk space...' &&
        docker image prune -f || true &&
        echo 'Importing new image into K3S containerd (bypasses Docker image store, which K3S cannot see)...' &&
        docker save airflow-dbt:$BUILD_TAG | sudo k3s ctr images import - &&
        echo 'Verifying image is visible to K3S...' &&
        sudo k3s ctr images list | grep airflow-dbt
    "
}
