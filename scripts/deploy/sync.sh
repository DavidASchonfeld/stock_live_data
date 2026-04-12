#!/bin/bash
# Module: sync — rsync file transfers and K8s secret/manifest application.
# Sourced by deploy.sh; all variables from common.sh are available here.

# rsync flags used throughout:
# -a: archive mode (preserves permissions and timestamps)
# -v: verbose (shows which files were transferred)
# -z: compress data in transit
# --progress: shows per-file progress bar and transfer speed
# Note: rsync does not respect .gitignore, so files like api_key.py, db_config.py, and constants.py are synced intentionally

step_sync_dags() {
    echo "=== Step 2: Syncing DAG files to EC2 ==="
    # Trailing "/" on source means "sync contents of folder", not the folder itself
    rsync -avz --progress "$PROJECT_ROOT/airflow/dags/" "$EC2_HOST:$EC2_DAG_PATH/"
}

step_sync_helm_dockerfile() {
    echo "=== Step 2b: Syncing Helm values to EC2 ==="
    rsync -avz --progress "$PROJECT_ROOT/airflow/helm/values.yaml" "$EC2_HOST:$EC2_HELM_PATH/"

    echo "=== Step 2b1: Syncing Airflow Dockerfile to EC2 ==="
    # Sync the Dockerfile so the image can be built on EC2 (image is built and loaded directly into K3S — it's never pushed to ECR)
    rsync -avz --progress "$PROJECT_ROOT/airflow/docker/" "$EC2_HOST:$EC2_HOME/airflow/docker/"
}

step_sync_manifests_secrets() {
    echo "=== Step 2c: Syncing Kubernetes manifests to EC2 ==="
    # These copies let you run kubectl commands directly on EC2 if you ever need to
    # (Git is still the master copy — these are just for convenience on the EC2 side)
    rsync -avz --progress "$PROJECT_ROOT/airflow/manifests/" "$EC2_HOST:$EC2_HOME/airflow/manifests/"
    rsync -avz --progress "$PROJECT_ROOT/dashboard/manifests/" "$EC2_HOST:$EC2_HOME/dashboard/manifests/"

    echo "=== Step 2c1: Applying K8s secrets (credentials) ==="
    # Apply Snowflake and database credential secrets to both airflow-my-namespace and default namespaces.
    # Must run before Step 2d (Helm upgrade) so pods can read their environment variables when they start.
    # These secret files are gitignored and never committed — they only exist locally and on EC2.
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

    echo "=== Step 2c1a: Patching SNOWFLAKE_ROLE + AIRFLOW_CONN_SNOWFLAKE_DEFAULT into snowflake-credentials secret ==="
    # SNOWFLAKE_ROLE is not stored in snowflake-secret.yaml, so we add it here on every deploy.
    # anomaly_detector.py reads this value from the environment at runtime.
    #
    # AIRFLOW_CONN_SNOWFLAKE_DEFAULT is also injected here.
    # Airflow 3 reads AIRFLOW_CONN_<CONN_ID> env vars at startup and auto-registers the connection —
    # this means SnowflakeHook(snowflake_conn_id="snowflake_default") works on a fresh install
    # without any manual setup in the Airflow UI.
    #
    # The JSON patch `add` operation creates the key if it doesn't exist, or updates it if it does — safe to run every time.
    ssh "$EC2_HOST" "
        # Read Snowflake credentials from the already-applied snowflake-credentials secret
        SF_ACCOUNT=\$(kubectl get secret snowflake-credentials -n airflow-my-namespace -o jsonpath='{.data.SNOWFLAKE_ACCOUNT}' | base64 -d) &&
        SF_USER=\$(kubectl get secret snowflake-credentials -n airflow-my-namespace -o jsonpath='{.data.SNOWFLAKE_USER}' | base64 -d) &&
        SF_PASS=\$(kubectl get secret snowflake-credentials -n airflow-my-namespace -o jsonpath='{.data.SNOWFLAKE_PASSWORD}' | base64 -d) &&

        # Build the Airflow connection in JSON format — SnowflakeHook 6.x reads 'account' from extra,
        # not from the URI host field. Using JSON ensures account is correctly set in extra.
        # JSON-escape the password to handle special characters safely.
        SF_PASS_ESC=\$(python3 -c \"import json, sys; print(json.dumps(sys.argv[1])[1:-1])\" \"\$SF_PASS\") &&
        CONN_URI=\"{\\\"conn_type\\\": \\\"snowflake\\\", \\\"login\\\": \\\"\$SF_USER\\\", \\\"password\\\": \\\"\$SF_PASS_ESC\\\", \\\"extra\\\": {\\\"account\\\": \\\"\$SF_ACCOUNT\\\", \\\"database\\\": \\\"PIPELINE_DB\\\", \\\"schema\\\": \\\"RAW\\\", \\\"warehouse\\\": \\\"PIPELINE_WH\\\", \\\"role\\\": \\\"PIPELINE_ROLE\\\"}}\" &&

        ROLE_B64=\$(printf 'PIPELINE_ROLE' | base64 -w0) &&
        CONN_B64=\$(printf '%s' \"\$CONN_URI\" | base64 -w0) &&

        kubectl patch secret snowflake-credentials -n airflow-my-namespace \
            --type=json \
            -p=\"[
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/SNOWFLAKE_ROLE\\\",\\\"value\\\":\\\"\$ROLE_B64\\\"},
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/AIRFLOW_CONN_SNOWFLAKE_DEFAULT\\\",\\\"value\\\":\\\"\$CONN_B64\\\"}
            ]\" &&
        kubectl patch secret snowflake-credentials -n default \
            --type=json \
            -p=\"[
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/SNOWFLAKE_ROLE\\\",\\\"value\\\":\\\"\$ROLE_B64\\\"},
                {\\\"op\\\":\\\"add\\\",\\\"path\\\":\\\"/data/AIRFLOW_CONN_SNOWFLAKE_DEFAULT\\\",\\\"value\\\":\\\"\$CONN_B64\\\"}
            ]\" &&
        echo 'SNOWFLAKE_ROLE + AIRFLOW_CONN_SNOWFLAKE_DEFAULT patched into both namespaces.'
    "

    echo "=== Step 2c2: Syncing dbt profiles secret to EC2 ==="
    # profiles.yml is gitignored (contains dbt connection config referencing Snowflake env vars).
    # scp copies the file to EC2, then kubectl creates or updates the dbt-profiles secret (safe to run multiple times).
    # The secret is mounted into the Airflow scheduler and workers at /dbt/ (configured in values.yaml).
    # Airflow tasks point dbt to that folder by setting DBT_PROFILES_DIR=/dbt.
    if [ -f "$PROJECT_ROOT/profiles.yml" ]; then
        scp "$PROJECT_ROOT/profiles.yml" "$EC2_HOST:$EC2_HOME/profiles.yml"
        ssh "$EC2_HOST" "kubectl create secret generic dbt-profiles \
            --from-file=profiles.yml=$EC2_HOME/profiles.yml \
            -n airflow-my-namespace \
            --dry-run=client -o yaml | kubectl apply -f -"
    else
        echo "Note: profiles.yml not found locally — skipping (create it first if dbt is not yet set up)."
    fi

    echo "=== Step 2c3: Deleting stale Airflow migration Job ==="
    # This Job was created before Helm was managing it, so it's missing the labels Helm expects to see.
    # With the old setup (useHelmHooks:true), Helm created this Job automatically. Now that we've switched
    # to useHelmHooks:false, Helm tries to take ownership of the existing Job and fails.
    # Safe to delete — the database migration already ran, and Helm will recreate the Job on the next upgrade if it needs to.
    ssh "$EC2_HOST" "kubectl delete job airflow-run-airflow-migrations -n airflow-my-namespace --ignore-not-found=true \
        && echo 'Migration Job cleared (safe to run multiple times).'"
}
