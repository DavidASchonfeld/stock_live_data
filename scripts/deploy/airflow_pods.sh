#!/bin/bash
# Module: airflow_pods — Helm upgrade, Airflow pod restarts (parallel waits), and ml-venv setup.
# Sourced by deploy.sh; BUILD_TAG must be set in deploy.sh before calling step_helm_upgrade.

step_helm_upgrade() {
    echo "=== Step 2d: Applying Helm values to live Airflow release ==="
    # Copying values.yaml to EC2 (step 2b) just puts the file there — it does NOT update the live Airflow deployment.
    # helm upgrade is what actually applies those changes (memory limits, worker count, probes) to the running pods.
    # Without this step, any values.yaml changes you make would be ignored until someone runs helm upgrade manually.
    #
    # --version 1.20.0: locks the Helm chart to the Airflow 3.x version (we upgraded from 1.15.0 on 2026-04-06)
    # No --reuse-values: we pass only values.yaml — using --reuse-values would pull in old 2.x Helm settings that break the 3.x schema
    # migrateDatabaseJob.useHelmHooks: false in values.yaml means helm upgrade returns right away —
    #   the database migration runs in the background, and pods wait for it using init containers
    # Note: each flag is on its own line with no inline comments — inside a double-quoted SSH string, bash does NOT
    #   strip # comments. They become literal text passed to helm, which breaks the command. --force would end up
    #   on its own line and be interpreted as a separate command ("command not found").
    # --set overrides the image tag in values.yaml with the fresh BUILD_TAG from this deploy, so K3S loads the new image
    ssh "$EC2_HOST" "helm upgrade airflow apache-airflow/airflow \
        -n airflow-my-namespace \
        --version 1.20.0 \
        --timeout 10m \
        --force \
        --set images.airflow.tag=$BUILD_TAG \
        -f $EC2_HELM_PATH/values.yaml"

    # Double-check that helm actually updated the image tag — force-update the StatefulSet directly if it didn't (helm can silently skip updates in some cases)
    ssh "$EC2_HOST" "
        ACTUAL_TAG=\$(kubectl get statefulset airflow-scheduler -n airflow-my-namespace \
            -o jsonpath='{.spec.template.spec.containers[?(@.name==\"scheduler\")].image}' 2>/dev/null || echo '')
        echo \"StatefulSet scheduler image after helm upgrade: \$ACTUAL_TAG\"
        if [ \"\$ACTUAL_TAG\" != 'airflow-dbt:$BUILD_TAG' ]; then
            echo 'WARNING: Helm did not update scheduler image — force-patching StatefulSet...'
            kubectl set image statefulset/airflow-scheduler \
                scheduler=airflow-dbt:$BUILD_TAG \
                -n airflow-my-namespace
        else
            echo 'OK: StatefulSet has the correct image tag.'
        fi
    "

    echo "=== Step 2e: Applying Airflow service manifest ==="
    # Re-apply the Airflow UI service so its pod selector stays in sync with any changes in values.yaml
    # (for example, component label renames between Airflow 2.x and 3.x).
    # Without this step, helm upgrade doesn't update our manually-created NodePort service, so any label changes would be silently ignored.
    ssh "$EC2_HOST" "kubectl apply -f $EC2_HOME/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace"
}

step_verify_airflow_image() {
    echo "=== Step 7a: Ensuring airflow image is still in K3S containerd ==="
    # K3S can automatically delete the 3.3 GiB Airflow image to free disk space if no containers are actively
    # using it and disk usage goes above ~85%. This can happen during the ~20 min gap between building the image
    # and restarting the Airflow pods, if the api-server init containers finish and the pods crash in the meantime.
    # Docker still has the image (we never prune it from Docker), so re-importing into K3S is fast.
    ssh "$EC2_HOST" "
        if sudo k3s ctr images list | grep -q 'airflow-dbt:$BUILD_TAG'; then
            echo 'airflow-dbt:$BUILD_TAG confirmed present in K3S containerd'
        else
            echo 'airflow-dbt:$BUILD_TAG not found — GC likely evicted it. Re-importing from Docker store...'
            docker save airflow-dbt:$BUILD_TAG | sudo k3s ctr images import -
            echo 'Re-import complete. Verifying...'
            sudo k3s ctr images list | grep airflow-dbt
        fi
    "
}

step_restart_airflow_pods() {
    echo "=== Step 7: Restarting Airflow pods to prevent stale DAG cache ==="
    # WHY this step is needed:
    #   After syncing new DAG files to EC2, the Airflow pods can hold a stale cached view of the
    #   /opt/airflow/dags/ folder. The DAG Processor pod in particular can still see the old file
    #   list even after the files on disk have been updated. This causes Airflow to flag newly
    #   deployed DAGs as stale and remove them from the UI after ~90 seconds.
    #
    #   Restarting the Scheduler and Processor pods forces Kubernetes to remount the DAG folder
    #   with a fresh view. This is the proven fix from the 2026-03-31 staleness incident.

    # Phase A: Delete all three pods in one SSH call — fast, synchronous
    ssh "$EC2_HOST" "
        echo 'Restarting Scheduler pod...' &&
        kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace --ignore-not-found=true &&
        echo 'Restarting DAG Processor pod(s)...' &&
        kubectl delete pod -l component=dag-processor -n airflow-my-namespace --ignore-not-found=true &&
        echo 'Restarting Triggerer pod...' &&
        kubectl delete pod airflow-triggerer-0 -n airflow-my-namespace --ignore-not-found=true &&
        echo 'All three pods deleted — waiting 10s for API server to register termination before watchers start...' &&
        sleep 10
    "

    # Phase B: Wait for all three pods at the same time — total wait time is at most 360s (one pod's timeout)
    # instead of up to 18 min if done one at a time. Since all three pods are already deleted and restarting
    # independently, we can wait for them simultaneously.
    echo "Waiting for Airflow pods to become Ready (parallel, up to 360s each)..."
    ssh "$EC2_HOST" "kubectl wait pod/airflow-scheduler-0 -n airflow-my-namespace --for=condition=Ready --timeout=360s" &
    local sched_pid=$!
    ssh "$EC2_HOST" "kubectl wait pod -l component=dag-processor -n airflow-my-namespace --for=condition=Ready --timeout=360s" &
    local dagproc_pid=$!
    ssh "$EC2_HOST" "kubectl wait pod/airflow-triggerer-0 -n airflow-my-namespace --for=condition=Ready --timeout=360s" &
    local trigger_pid=$!

    _wait_bg $sched_pid   "airflow-scheduler-0 Ready"
    _wait_bg $dagproc_pid "dag-processor Ready"
    _wait_bg $trigger_pid "airflow-triggerer-0 Ready"
    echo "All Airflow pods Ready."

    # Phase C: Post-restart verification and variable setup (scheduler is confirmed Ready)
    ssh "$EC2_HOST" "
        echo 'Verifying DAGs are visible...' &&
        kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list &&
        # Set Airflow variables now that the scheduler is confirmed Ready — these are stored in the database and survive pod restarts
        echo 'Setting Airflow variables...' &&
        kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
            airflow variables set KAFKA_BOOTSTRAP_SERVERS kafka.kafka.svc.cluster.local:9092 &&
        echo '  KAFKA_BOOTSTRAP_SERVERS set.'
    " || {
        echo ""
        echo "WARNING: Airflow DAG verification or variable setup failed. Check manually."
        ssh "$EC2_HOST" "kubectl get pods -n airflow-my-namespace"
    }
}

step_setup_ml_venv() {
    echo "=== Step 7b: Creating/updating ml-venv in Airflow scheduler pod ==="
    # anomaly_detector.py uses /opt/ml-venv/bin/python directly — this virtual environment must exist before the DAG runs.
    # /opt/ inside the container is temporary — it gets wiped every time the pod restarts — so we rebuild
    # the venv here after every pod restart. This also means any package version changes we make here take
    # effect immediately, without needing to rebuild the Docker image.
    # We create an isolated venv (no --system-site-packages) to avoid conflicts with Airflow's own Python packages.
    ssh "$EC2_HOST" "
        echo 'Creating ml-venv if it does not already exist...' &&
        kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
            python3 -m venv /opt/ml-venv &&

        echo 'Installing ML packages into ml-venv (pinned to match Dockerfile — no cache, venv is rebuilt fresh every deploy)...' &&
        # chardet<6: version 6+ causes a version mismatch warning from the requests library; we pin it to match the Dockerfile
        kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
            /opt/ml-venv/bin/pip install --quiet --no-cache-dir \
                \"mlflow==2.15.1\" \
                \"scikit-learn==1.5.2\" \
                \"pandas==2.2.2\" \
                \"snowflake-connector-python==3.10.1\" \
                \"setuptools<75\" \
                \"requests>=2.32.0\" \
                \"chardet>=3.0.2,<6\" &&

        echo 'Verifying all required packages are importable from ml-venv...' &&
        kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
            /opt/ml-venv/bin/python -c \
                'import sklearn, mlflow, snowflake.connector, pandas; print(\"ml-venv OK — all packages importable\")' &&

        echo 'ml-venv ready at /opt/ml-venv'
    " || {
        echo ""
        echo "WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved."
        echo "Diagnose with: kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/pip list"
    }
}
