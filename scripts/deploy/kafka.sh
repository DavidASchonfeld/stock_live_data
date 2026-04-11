#!/bin/bash
# Module: kafka — Kafka manifest sync, image pre-pull, StatefulSet deploy, and topic creation.
# Sourced by deploy.sh; all variables from common.sh are available here.

step_deploy_kafka() {
    echo "=== Step 2b3: Syncing Kafka manifests to EC2 ==="
    # we use a plain Kubernetes manifest here instead of the old Bitnami Helm chart (simpler, no licensing issues)
    rsync -avz --progress "$PROJECT_ROOT/kafka/k8s/" "$EC2_HOST:$EC2_HOME/kafka/k8s/"

    echo "=== Step 2b3a: Pre-pulling Kafka image into K3s containerd ==="
    # Pre-loads the Kafka image before the pod starts, so the rollout doesn't fail the 480s timeout waiting on a slow download.
    # Same approach used for MLflow and the Airflow image. crictl pull does nothing if the image is already there.
    ssh "$EC2_HOST" "
        sudo k3s crictl pull docker.io/apache/kafka:4.0.0 \
        && echo 'Kafka image ready in K3s containerd.'
    "

    echo "=== Step 2b4: Deploying Kafka to K3s (safe to run multiple times) ==="
    # kubectl apply creates Kafka if it doesn't exist, or updates it if it does — safe to run every time.
    # Kafka lives in its own 'kafka' namespace, separate from airflow-my-namespace.
    ssh "$EC2_HOST" "
        # Create kafka namespace if it doesn't exist
        kubectl create namespace kafka --dry-run=client -o yaml | kubectl apply -f -

        # Apply StatefulSet + Services from the plain manifest
        kubectl apply -f $EC2_HOME/kafka/k8s/kafka.yaml \
        && echo 'Kafka manifests applied.'

        # Deadlock guard: Kubernetes won't replace a pod that's already Not-Ready, even after you apply a config change.
        # kubectl apply updates Kubernetes's internal database (etcd) but the running pod doesn't change until it's restarted.
        # We detect this stuck state (the desired version differs from the running version, and the pod is Not-Ready)
        # and delete the pod so Kubernetes can start a fresh one with the correct config.
        CURRENT_REV=\$(kubectl get statefulset kafka -n kafka \
            -o jsonpath='{.status.currentRevision}' 2>/dev/null || echo '')
        UPDATE_REV=\$(kubectl get statefulset kafka -n kafka \
            -o jsonpath='{.status.updateRevision}' 2>/dev/null || echo '')
        POD_READY=\$(kubectl get pod kafka-0 -n kafka \
            -o jsonpath='{.status.conditions[?(@.type==\"Ready\")].status}' 2>/dev/null || echo '')

        if [ -n \"\$CURRENT_REV\" ] && [ -n \"\$UPDATE_REV\" ] \
            && [ \"\$CURRENT_REV\" != \"\$UPDATE_REV\" ] && [ \"\$POD_READY\" = False ]; then
            echo \"DEADLOCK DETECTED: pending update (\$CURRENT_REV -> \$UPDATE_REV), kafka-0 Not Ready.\"
            echo 'Gracefully deleting kafka-0 to let controller apply new spec...'
            # 30-second grace period gives Kafka time to flush its data to disk, avoiding a slow recovery scan on the next startup
            kubectl delete pod kafka-0 -n kafka --grace-period=30
            # Wait for the old pod to fully stop before we start watching for the new one — otherwise we might watch the wrong pod
            kubectl wait pod/kafka-0 -n kafka --for=delete --timeout=60s \
                || echo 'Note: kafka-0 took > 60s to terminate — continuing anyway.'
        else
            echo \"No deadlock (currentRevision=\$CURRENT_REV, updateRevision=\$UPDATE_REV, podReady=\$POD_READY).\"
        fi

        # Wait for the rollout to fully complete.
        # We use rollout status (not kubectl wait) because kubectl wait can mistakenly return success for the OLD pod
        # right before it's deleted. rollout status specifically waits for the NEW pod to be ready.
        # 480s timeout: Kafka's startup health check can take up to 290s (20s initial delay + 18 retries × 15s),
        # plus extra buffer for scheduling and first readiness.
        echo 'Waiting for Kafka rollout to complete (readiness probe gates on port 9092)...'
        kubectl rollout status statefulset/kafka -n kafka --timeout=480s \
        || {
            echo 'WARNING: Kafka rollout did not complete — skipping topic creation. Run deploy again once it is running.'
            # Look at the exit code to understand why it failed:
            # exit 137 means Kubernetes killed it for using too much memory (OOMKill)
            # exit 1 means Kafka started too slowly and the health check timed out
            echo '--- kafka-0 pod conditions and last state ---'
            kubectl describe pod kafka-0 -n kafka \
                | grep -E 'Last State|Exit Code|OOMKilled|Conditions|Ready|Started|Finished|Reason'
            echo '--- kafka-0 current logs (last 30 lines) ---'
            kubectl logs kafka-0 -n kafka --tail=30 2>/dev/null \
                || kubectl logs kafka-0 -n kafka --previous --tail=30 2>/dev/null \
                || echo '(no logs available — pod may not have started)'
            exit 0
        }

        # Create topics — --if-not-exists means Kafka skips creation if the topic is already there — safe to run every time
        # the kafka-topics.sh script is at /opt/kafka/bin/ in this image — it's not on the PATH the way it was in the old Bitnami image
        kubectl exec kafka-0 -n kafka -- /opt/kafka/bin/kafka-topics.sh \
            --bootstrap-server localhost:9092 --create --if-not-exists \
            --topic stocks-financials-raw --partitions 1 --replication-factor 1 \
        && echo 'Topic stocks-financials-raw ready.'

        kubectl exec kafka-0 -n kafka -- /opt/kafka/bin/kafka-topics.sh \
            --bootstrap-server localhost:9092 --create --if-not-exists \
            --topic weather-hourly-raw --partitions 1 --replication-factor 1 \
        && echo 'Topic weather-hourly-raw ready.'

        echo 'Kafka topics:'
        kubectl exec kafka-0 -n kafka -- /opt/kafka/bin/kafka-topics.sh \
            --list --bootstrap-server localhost:9092
    "
}
