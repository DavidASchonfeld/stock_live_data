#!/bin/bash
# Module: airflow_pods — Helm upgrade, Airflow pod restarts (parallel waits), and ml-venv setup.
# Sourced by deploy.sh; BUILD_TAG must be set in deploy.sh before calling step_helm_upgrade.

_wait_scheduler_exec() {
    # Poll until kubectl exec can actually reach the scheduler container — pod Ready condition is not enough.
    # The K3S container runtime needs a few extra seconds after the pod turns Ready before exec connections succeed.
    # Called before every kubectl exec into the scheduler so each step gets its own readiness confirmation.
    ssh "$EC2_HOST" "
        for i in \$(seq 1 30); do
            if kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /bin/true 2>/dev/null; then
                echo \"Scheduler container exec-ready (attempt \$i)\"
                break
            fi
            if [ \$i -eq 30 ]; then
                echo 'ERROR: Scheduler container did not become exec-ready after 60s'
                exit 1
            fi
            sleep 2
        done
    "
}

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

    # Phase B: Wait for all three pods at the same time — total wait time is at most 1000s (scheduler's timeout)
    # instead of up to 50 min if done one at a time. Since all three pods are already deleted and restarting
    # independently, we can wait for them simultaneously.
    # Scheduler: 1000s — startup probe now allows up to 30×60s=1800s (failureThreshold raised from 15→30);
    # with 200m CPU request the scheduler typically starts in <300s, so 1000s is ample.
    # dag-processor/triggerer: 600s — lighter pods without the heavy provider-load startup probe.
    echo "Waiting for Airflow pods to become Ready (parallel)..."
    ssh "$EC2_HOST" "kubectl wait pod/airflow-scheduler-0 -n airflow-my-namespace --for=condition=Ready --timeout=1000s" &
    local sched_pid=$!
    ssh "$EC2_HOST" "kubectl wait pod -l component=dag-processor -n airflow-my-namespace --for=condition=Ready --timeout=600s" &
    local dagproc_pid=$!
    ssh "$EC2_HOST" "kubectl wait pod/airflow-triggerer-0 -n airflow-my-namespace --for=condition=Ready --timeout=600s" &
    local trigger_pid=$!

    # Print pod state + recent logs automatically if scheduler fails — avoids needing to SSH in manually
    _wait_bg $sched_pid "airflow-scheduler-0 Ready" || {
        ssh "$EC2_HOST" "kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace | tail -50" || true
        ssh "$EC2_HOST" "kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=30 2>/dev/null" || true
        exit 1
    }
    _wait_bg $dagproc_pid "dag-processor Ready"
    _wait_bg $trigger_pid "airflow-triggerer-0 Ready"
    echo "All Airflow pods Ready."

    # Phase B.5: Poll until scheduler container is exec-able
    # kubectl wait --for=condition=Ready only checks the pod condition — the container runtime needs
    # a few extra seconds before kubectl exec can actually reach the container by name.
    echo "Waiting for scheduler container to accept exec connections..."
    _wait_scheduler_exec

    # Phase B.6: Verify scheduler is running via port 8793 (Airflow 3.x internal execution API server).
    # Port 8974 (Airflow 2.x HTTP health server) no longer exists in Airflow 3.x — curl on 8974 exits 7.
    # pgrep -f 'airflow scheduler' exits 1 — Airflow 3.x scheduler process name doesn't match that pattern.
    # Instead: Airflow 3.x scheduler pods run a uvicorn/FastAPI internal API on port 8793.
    # curl exits 0 for any HTTP response, exits 7 only if nothing is listening — zero Python overhead, no OOM risk.

    # Phase C1: Scheduler health check with retry — confirm port 8793 is accepting connections after exec-readiness.
    # IMPORTANT: ssh command uses '|| exit_code=$?' instead of a bare next-line '$?' capture.
    # deploy.sh sets 'set -euo pipefail', so a bare non-zero ssh exit code triggers immediate script
    # exit before the next line can run — the retry loop was being bypassed entirely on exit 137.
    # The '||' pattern prevents set -e from firing while still capturing the real exit code.
    echo "Verifying scheduler health (with retry)..."
    local dags_ok=0
    for attempt in 1 2 3 4 5; do
        # curl without -f: exits 0 for any HTTP response (200/401/404), exits 7 if port not listening
        local exit_code=0
        ssh "$EC2_HOST" "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- curl -s --max-time 10 -o /dev/null http://localhost:8793/" || exit_code=$?
        if [ $exit_code -eq 0 ]; then
            dags_ok=1
            break
        fi
        # Exit 137 = SIGKILL — curl adds no Python overhead so this means an unrelated container restart mid-check
        if [ $exit_code -eq 137 ]; then
            echo "  Health check attempt $attempt/5 — container was killed (exit 137). Waiting 15s for restart..."
            sleep 15
        else
            echo "  Health check attempt $attempt/5 failed (exit $exit_code) — retrying in 10s..."
            sleep 10
        fi
    done
    if [ "$dags_ok" -eq 0 ]; then
        echo ""
        echo "WARNING: airflow health failed after 5 attempts. Scheduler may not be ready — check scheduler logs."
        # Print recent scheduler logs to surface the actual error without needing to SSH in manually
        ssh "$EC2_HOST" "kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=30 2>/dev/null || true"
    fi

    # Variables (KAFKA_BOOTSTRAP_SERVERS, MLFLOW_TRACKING_URI) are injected via AIRFLOW_VAR_* in values.yaml.
    # kubectl exec airflow variables set OOM-kills (exit 137) the scheduler on Airflow 3.x — importing the
    # full provider stack spikes memory past the 2Gi container limit. Env var injection avoids that entirely.

    # Phase D: Reset Kafka consumer group offsets to latest.
    # After any pod restart or fresh deploy, committed offsets are lost. Both consumer groups use
    # auto_offset_reset="latest" with enable_auto_commit=False (manual commit). Without a committed
    # offset, the consumer seeks to the end of the topic at connect time — after the producer has
    # already published. The consumer polls for 30s, finds nothing, commits nothing, and exits with
    # 0 records. Every subsequent run repeats this cycle silently: dbt and anomaly detection are
    # always skipped. Resetting to --to-latest here positions each group at the current end of the
    # topic so the NEXT message the producer publishes is the one the consumer reads.
    # Note: --to-earliest is NOT used — the weather topic has old corrupt messages near offset 0 that
    # cause JSONDecodeError during deserialization.
    echo "=== Resetting Kafka consumer group offsets to latest ==="
    ssh "$EC2_HOST" "
        kubectl exec kafka-0 -n kafka -- \
            /opt/kafka/bin/kafka-consumer-groups.sh \
            --bootstrap-server localhost:9092 \
            --group stocks-consumer-group \
            --reset-offsets --to-latest \
            --topic stocks-financials-raw --execute &&
        kubectl exec kafka-0 -n kafka -- \
            /opt/kafka/bin/kafka-consumer-groups.sh \
            --bootstrap-server localhost:9092 \
            --group weather-consumer-group \
            --reset-offsets --to-latest \
            --topic weather-hourly-raw --execute &&
        echo 'Kafka consumer group offsets reset to latest.'
    " || echo "WARNING: Kafka offset reset failed — run Steps 8 and 10 of RESTORE_VERIFICATION.md manually before triggering pipelines."
}

step_setup_ml_venv() {
    echo "=== Step 7b: Creating/updating ml-venv in Airflow scheduler pod ==="
    # anomaly_detector.py uses /opt/ml-venv/bin/python directly — this virtual environment must exist before the DAG runs.
    # /opt/ inside the container is temporary — it gets wiped every time the pod restarts — so we rebuild
    # the venv here after every pod restart. This also means any package version changes we make here take
    # effect immediately, without needing to rebuild the Docker image.
    # We create an isolated venv (no --system-site-packages) to avoid conflicts with Airflow's own Python packages.
    # Re-verify exec-readiness — the container may have briefly lost its exec connection since step_restart_airflow_pods ran
    _wait_scheduler_exec

    ssh "$EC2_HOST" "
        # Fast path: use pip show (reads metadata only, no imports) to avoid OOM-killing the scheduler.
        # Importing all 4 ML packages simultaneously in a running scheduler pod spikes ~500-800 MB — enough
        # to exceed the 2 Gi container limit and produce a false exit 137 that triggers an unnecessary rebuild.
        if kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
            /opt/ml-venv/bin/pip show mlflow scikit-learn snowflake-connector-python pandas setuptools > /dev/null 2>&1; then
            echo 'ml-venv package check passed (pip show) — skipping reinstall'
            echo 'ml-venv ready at /opt/ml-venv'
        else
            # Fallback: venv is missing or broken (e.g., image mismatch, container corruption) — rebuild
            # --upgrade: idempotent — reinitialises an existing venv dir without wiping site-packages
            echo 'ml-venv missing or broken — rebuilding...' &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                python3 -m venv --upgrade /opt/ml-venv &&

            # Install one package at a time — avoids a single large pip resolver memory spike that OOM-kills the container
            # chardet<6: version 6+ causes a version mismatch warning from requests; pin to match Dockerfile
            echo 'Installing ML packages into ml-venv (one at a time to reduce memory pressure)...' &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"mlflow==2.15.1\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"scikit-learn==1.5.2\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"pandas==2.2.2\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"snowflake-connector-python==3.10.1\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"setuptools<75\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"requests>=2.32.0\" &&
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip install --no-cache-dir \"chardet>=3.0.2,<6\" &&

            # Confirm all packages are present after rebuild
            kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
                /opt/ml-venv/bin/pip show mlflow scikit-learn snowflake-connector-python pandas setuptools > /dev/null &&

            echo 'ml-venv ready at /opt/ml-venv'
        fi
    " || {
        echo ""
        echo "WARNING: ml-venv setup failed. anomaly_detector.py will not run until this is resolved."
        echo "If pip install keeps OOM-killing the container, a full redeploy (Docker image rebuild) is required."
        echo "Re-run without a full redeploy: ./scripts/deploy.sh --fix-ml-venv"
        echo "Diagnose with: kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- /opt/ml-venv/bin/pip list"
    }
}
