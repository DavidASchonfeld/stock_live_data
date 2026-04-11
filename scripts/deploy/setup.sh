#!/bin/bash
# Module: setup — EC2 directory prep, kubectl permissions, and pre-flight DAG validation.
# Sourced by deploy.sh; all variables from common.sh are available here.

step_setup() {
    echo "=== Step 1: Ensuring target directories exist on EC2 ==="
    ssh "$EC2_HOST" "mkdir -p $EC2_DAG_PATH $EC2_HELM_PATH $EC2_BUILD_PATH $EC2_DASHBOARD_PATH/manifests $EC2_HOME/airflow/dag-mylogs $EC2_HOME/airflow/docker $EC2_HOME/kafka/k8s \
        && chmod 777 $EC2_HOME/airflow/dag-mylogs"  # 777 gives the Airflow pod (which runs as user 50000) permission to write logs to this folder

    echo "=== Step 1c: Ensuring kubectl config is accessible ==="
    # K3s stores its cluster config at /etc/rancher/k3s/k3s.yaml (not ~/.kube/config like a normal kubectl install).
    # K3s creates this file as root-only by default, so we open it up (chmod 644) so the ubuntu user can read it.
    # We do this on every deploy because K3s resets the file permissions when it restarts.
    ssh "$EC2_HOST" "sudo chmod 644 /etc/rancher/k3s/k3s.yaml"

    echo "=== Step 1b: Pre-flight validation ==="

    # Validate Python syntax in all DAG files (catches typos, indentation errors, missing colons)
    # We check the exit code (not the output text) because py_compile signals errors by exiting with a non-zero code, which is more reliable than grepping the output
    echo "Checking Python syntax in DAG files..."
    if find "$PROJECT_ROOT/airflow/dags" -name "*.py" | xargs python3 -m py_compile 2>/dev/null; then
        echo "✓ All DAG files have valid Python syntax"
    else
        echo "✗ Syntax error in DAG files. Fix before deploying."
        find "$PROJECT_ROOT/airflow/dags" -name "*.py" | xargs python3 -m py_compile  # run again without silencing output, so the error message is visible
        exit 1
    fi

    # Validate that all DAG imports work (catches missing modules, missing secrets, etc.)
    # The parentheses ( ) create a subshell so the cd doesn't affect the rest of the script
    echo "Validating module imports..."
    (
        cd "$PROJECT_ROOT/airflow/dags"
        python3 << 'VALIDATION_EOF'
import sys
sys.path.insert(0, '.')  # Add the current folder to the Python path, which is what the Airflow pod does at /opt/airflow/dags

# Skip import check if airflow is not installed locally (only available inside the pod)
try:
    import airflow
except ImportError:
    print("⚠ airflow not installed locally — skipping import validation (syntax already verified above)")
    sys.exit(0)

# Try importing all DAG files
dag_files = ['dag_stocks', 'dag_weather', 'dag_staleness_check', 'dag_stocks_consumer', 'dag_weather_consumer']
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
    )

    echo ""
}
