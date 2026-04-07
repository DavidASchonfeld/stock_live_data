# Deploy Issues

Troubleshooting deploy.sh failures, Docker/ECR problems, DAG file sync issues, and deployment validation.

**See also:** [Parent index](../TROUBLESHOOTING.md) | [DEBUGGING.md](../DEBUGGING.md) | [RUNBOOKS.md](../RUNBOOKS.md)

---

## Issue: Warnings in `./scripts/deploy.sh` output

### Symptoms (resolved in April 2026 — documented for reference)

Four warning categories appeared during deploy:

```
WARNING! Your credentials are stored unencrypted in '/home/ubuntu/.docker/config.json'.
DEPRECATED: The legacy builder is deprecated and will be removed in a future release.
RequestsDependencyWarning: urllib3 (2.6.3) or chardet .../charset_normalizer ... doesn't match a supported version!
RemovedInAirflow4Warning: The airflow.security.permissions module is deprecated
```

### Root Causes & Fixes Applied

| Warning | Root Cause | Fix |
|---------|-----------|-----|
| Unencrypted Docker credentials | `docker login` writes ECR tokens to `~/.docker/config.json` in plaintext | Switched to `amazon-ecr-credential-helper` — fetches tokens from IAM role on demand, nothing stored on disk |
| Legacy Docker builder | Default `docker build` uses the old build engine | Added `DOCKER_BUILDKIT=1` env var to the docker build command in Step 4 |
| `RequestsDependencyWarning` | Older `requests` version didn't declare support for `urllib3 2.x` | Pinned `requests>=2.32.3` in `_PIP_ADDITIONAL_REQUIREMENTS` in `values.yaml` |
| `RemovedInAirflow4Warning` | `apache-airflow-providers-common-compat` (Airflow's own compat shim) was calling the deprecated `airflow.security.permissions` module | Upgraded `apache-airflow-providers-common-compat>=1.5.0` in `_PIP_ADDITIONAL_REQUIREMENTS` |

### Notes
- The `requests` and `providers-common-compat` warnings came from **Airflow's own provider packages**, not our DAG code — DAG files were fully audited and are clean
- The ECR credential helper (`amazon-ecr-credential-helper`) is the [AWS-recommended approach](https://docs.aws.amazon.com/AmazonECR/latest/userguide/registry_auth.html) for ECR authentication; it uses the EC2 instance's IAM role and requires no stored credentials
- `_PIP_ADDITIONAL_REQUIREMENTS` installs packages at every pod startup — this adds ~15–30s to restart time but is appropriate for this project; a custom Airflow image would be faster for high-churn environments

---

## How Deploy.sh Validates DAG Files (Deployment Best Practices)

### Pre-flight Checks

When you run `./scripts/deploy.sh`, **before syncing to EC2**, it validates:

1. **Python syntax** — Catches typos, indentation errors, missing colons
   ```bash
   ✓ All DAG files have valid Python syntax
   ```

2. **Module imports** — Catches missing local modules (stock_client, file_logger, etc.)
   ```bash
   ✓ dag_stocks imports successfully
   ✓ dag_weather imports successfully
   ```

3. **Secret injection** — Each DAG validates that required Kubernetes secrets are available:
   ```python
   # In dag_stocks.py and dag_weather.py (added after imports):
   _required_secrets = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"]
   _missing_secrets = [k for k in _required_secrets if not os.getenv(k)]
   if _missing_secrets:
       raise RuntimeError(f"Missing Kubernetes secrets: {_missing_secrets}")
   ```

### Why This Matters

**Without validation:**
- Deploy file → pod starts but crashes → CrashLoopBackOff → read 200 lines of logs → find typo → fix locally → redeploy → repeat

**With validation:**
- Deploy file → validation fails locally → see 5-line error → fix → redeploy → success

This shifts debugging from "hours in logs" to "minutes locally".

### If Validation Fails

1. **Syntax error** — Check the Python file for typos, mismatched quotes, indentation
2. **Import error** — Verify the missing module exists in `airflow/dags/`
3. **Secret error** — Kubernetes secret not mounted; run in pod: `kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace` and check environment variables section

---

## Issue: DAG Files Not Visible in Airflow Pod

### Symptoms
- DAG files exist on EC2 but don't appear in the pod
- Airflow doesn't recognize new DAGs
- Scheduler logs show no errors, but DAGs don't appear in UI

### Diagnosis Steps

1. **Verify files exist on EC2**:
   ```bash
   ssh ec2-stock ls -la /home/ubuntu/airflow/dags/
   ```

2. **Check what's in the pod**:
   ```bash
   ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls -la /opt/airflow/dags/
   ```

3. **Compare the files** — Do they match?
   - If not, proceed to step 4
   - If yes, the issue is in Airflow's DAG parsing, not the volume mount

4. **Check PersistentVolume configuration**:
   ```bash
   ssh ec2-stock kubectl describe pv dag-pv
   ```
   Look for: `Source: HostPath: Path:`

5. **Compare paths**:
   - What does deploy.sh sync to? Check `scripts/deploy.sh` line 33:
     ```bash
     rsync -avz --progress airflow/dags/ "$EC2_HOST:$EC2_DAG_PATH/"
     # EC2_DAG_PATH is defined on line 9
     ```
   - What is the PV pointing to? From step 4 above
   - **Are they the same?** If not, this is your issue.

### Solution: Fix PersistentVolume Path

If PV is pointing to wrong path, delete and recreate it:

```bash
# 1. Delete the PVC (will cascade unbind from PV)
ssh ec2-stock kubectl delete pvc dag-pvc -n airflow-my-namespace

# 2. Remove finalizers from PV (makes it deletable)
ssh ec2-stock kubectl patch pvc dag-pvc -n airflow-my-namespace \
  --type merge -p '{"metadata":{"finalizers":null}}'

# 3. Force delete the PV
ssh ec2-stock kubectl delete pv dag-pv --grace-period=0 --force

# 4. Update the manifest with correct path
# Edit: airflow/manifests/pv-dags.yaml
# Change: hostPath.path to match deploy.sh sync destination

# 5. Recreate PV and PVC
ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/pv-dags.yaml
ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/pvc-dags.yaml

# 6. Restart scheduler pod
ssh ec2-stock kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace

# 7. Verify files appear
sleep 10
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- \
  ls -la /opt/airflow/dags/
```

---

## Issue: Deploy.sh Changes Not Reflected in Cluster

### Possible Causes

1. **DAG files synced, but PV pointing to old location** → See "DAG Files Not Visible in Airflow Pod" above

2. **values.yaml changed but `helm upgrade` not run** → deploy.sh Step 2d handles this automatically. Syncing the file to EC2 does NOT apply the changes to the live cluster — only `helm upgrade` does:
   ```bash
   ./scripts/deploy.sh  # includes Step 2d: helm upgrade
   # Or run manually on EC2:
   ssh ec2-stock "helm upgrade airflow apache-airflow/airflow -n airflow-my-namespace --version 1.20.0 --atomic=false --timeout 2m -f ~/airflow/helm/values.yaml"
   ```

3. **Kubernetes manifests not applied** → Run:
   ```bash
   # From Mac:
   ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/

   # Or manually apply specific manifests:
   ssh ec2-stock kubectl apply -f /home/ubuntu/airflow/manifests/pv-dags.yaml
   ```

3. **Scheduler pod needs restart** → Run:
   ```bash
   ssh ec2-stock kubectl rollout restart statefulset/airflow-scheduler -n airflow-my-namespace
   ```

4. **ECR credentials expired** (for Flask dashboard):
   ```bash
   # deploy.sh handles this automatically, but you can refresh manually:
   ssh ec2-stock "
   aws ecr get-login-password --region us-east-1 \
     | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com
   "
   ```



**See also:** [Docker Build Issues](docker-build-issues.md) for BuildKit/buildx problems.
