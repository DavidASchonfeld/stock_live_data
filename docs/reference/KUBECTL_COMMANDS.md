# Kubernetes (kubectl) Commands Reference

This guide documents common kubectl operations for managing the Stock Live Data Kubernetes cluster on EC2.

---

## Prerequisites

### SSH Tunnel Setup (Required for Mac)

Before running any kubectl commands on your Mac, establish an SSH tunnel to the EC2 K3S API server:

```bash
# Start SSH tunnel for kubectl (port 6443) and service NodePorts
ssh -N -L 6443:localhost:6443 \
    -L 30080:localhost:30080 \
    -L 32147:localhost:32147 \
    ec2-stock

# Leave this running in the background; open a new terminal for kubectl commands
```

### Kubeconfig Configuration

Kubeconfig is automatically set up by the deployment process. Verify it's in place:

```bash
# Check current kubectl context
kubectl config current-context
# Expected: "default" (pointing to EC2 K3S cluster)

# Test connectivity
kubectl cluster-info
```

If not configured, copy kubeconfig from EC2:

```bash
mkdir -p ~/.kube
ssh ec2-stock 'cat ~/.kube/config' > ~/.kube/config
chmod 600 ~/.kube/config
```

---

## Common Commands

### Viewing Resources

**List all pods in all namespaces:**
```bash
kubectl get pods --all-namespaces
```

**List pods in a specific namespace:**
```bash
kubectl get pods -n airflow-my-namespace
kubectl get pods -n default
```

**Show detailed pod information with labels:**
```bash
kubectl get pods -n airflow-my-namespace --show-labels
```

**Watch pods in real-time:**
```bash
# Auto-refreshes every 2s, press Ctrl+C to exit
kubectl get pods -n default -w
```

**View all services and their endpoints:**
```bash
kubectl get endpoints --all-namespaces
# Check if endpoints are populated (not "<none>") — if "<none>", service selector doesn't match pods
```

**Describe a resource (detailed info):**
```bash
kubectl describe pod my-kuber-pod-flask -n default
kubectl describe svc airflow-service-expose-ui-port -n airflow-my-namespace
```

---

### Deploying & Managing Manifests

**Apply a manifest (create or update resource):**
```bash
# From your Mac (Git is source of truth)
kubectl apply -f airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace
kubectl apply -f dashboard/manifests/pod-flask.yaml -n default
```

**Apply all manifests in a directory:**
```bash
kubectl apply -f airflow/manifests/ -n airflow-my-namespace
```

**Apply from EC2 (using synced copies):**
```bash
ssh ec2-stock
kubectl apply -f /home/ec2-user/airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace
kubectl apply -f /home/ec2-user/dashboard/manifests/pod-flask.yaml -n default
```

**Delete a resource:**
```bash
kubectl delete pod my-kuber-pod-flask -n default
kubectl delete svc airflow-service-expose-ui-port -n airflow-my-namespace
```

**Replace/recreate a pod:**
```bash
# Delete and immediately recreate
kubectl delete pod my-kuber-pod-flask -n default
kubectl apply -f dashboard/manifests/pod-flask.yaml -n default
```

---

### Logs & Debugging

**View pod logs (last 50 lines):**
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50
kubectl logs my-kuber-pod-flask -n default
```

**Follow logs in real-time:**
```bash
kubectl logs -f my-kuber-pod-flask -n default
# Press Ctrl+C to stop
```

**Logs from a specific container in a multi-container pod:**
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace -c scheduler
```

**View init container logs (useful for startup problems):**
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace -c wait-for-airflow-migrations
```

**Execute a command inside a pod:**
```bash
# Interactive shell
kubectl exec -it airflow-scheduler-0 -n airflow-my-namespace -- bash

# Single command
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags list
```

---

### Managing Secrets

**Create a generic secret:**
```bash
kubectl create secret generic db-credentials \
  -n default \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=your_password \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=10.0.1.50 \
  --dry-run=client -o yaml | kubectl apply -f -
```

**List secrets in a namespace:**
```bash
kubectl get secrets -n default
kubectl get secrets -n airflow-my-namespace
```

**View a secret (base64-encoded):**
```bash
kubectl get secret db-credentials -n default -o yaml
```

**Delete a secret:**
```bash
kubectl delete secret db-credentials -n default
```

---

## Troubleshooting Workflows

### Pod Won't Start (ImagePullBackOff)

**Diagnose:**
```bash
# See the exact error
kubectl describe pod my-kuber-pod-flask -n default
# Check the "Events:" section at the bottom
```

**Common causes:**
- ECR authentication expired (pods trying to pull from AWS ECR)
- Image tag doesn't exist or was deleted
- Wrong image registry URI

**Fix:**
```bash
# Refresh ECR credentials and redeploy
ssh "$EC2_HOST" "aws ecr get-login-password --region us-east-1 \
    | docker login --username AWS --password-stdin <AWS_ACCOUNT_ID>.dkr.ecr.<AWS_REGION>.amazonaws.com"

# Then delete and recreate the pod
kubectl delete pod my-kuber-pod-flask -n default
kubectl apply -f dashboard/manifests/pod-flask.yaml -n default
```

### Service Unreachable (Endpoints showing "<none>")

**Diagnose:**
```bash
# Check what selector the service is looking for
kubectl describe svc airflow-service-expose-ui-port -n airflow-my-namespace

# Check what labels the actual pods have
kubectl get pods -n airflow-my-namespace --show-labels
# Compare the service selector to pod labels
```

**Fix (patch service selector):**
```bash
# Update the service to match actual pod labels
kubectl patch svc airflow-service-expose-ui-port \
  -n airflow-my-namespace \
  --type='json' \
  -p='[{"op":"replace","path":"/spec/selector/component","value":"api-server"}]'

# Verify endpoints now show an IP
kubectl get endpoints airflow-service-expose-ui-port -n airflow-my-namespace
```

### Pods in Init State Forever (Init:0/1)

**Diagnose (check dependencies first):**
```bash
# PostgreSQL blocks other Airflow pods
kubectl get pods -n airflow-my-namespace | grep postgresql
# If not Running, that's the root cause

# Check init container logs
kubectl logs airflow-scheduler-0 -n airflow-my-namespace -c wait-for-airflow-migrations
```

**Fix:**
- Fix PostgreSQL first (usually image pull issues)
- Other pods will automatically unblock once dependencies are healthy

---

## Namespace Management

### Two Namespaces in This Project

| Namespace | Purpose | kubectl context |
|---|---|---|
| `airflow-my-namespace` | Airflow scheduler, triggerer, webserver, PostgreSQL, DAG volumes | Default context on EC2 |
| `default` | Flask/Dash pod, Flask service | Requires explicit `-n default` |

### Commands Requiring Explicit Namespace

```bash
# ALWAYS specify -n default for Flask resources
kubectl get pods -n default
kubectl apply -f dashboard/manifests/pod-flask.yaml -n default

# ALWAYS specify -n airflow-my-namespace for Airflow resources
kubectl get pods -n airflow-my-namespace
kubectl apply -f airflow/manifests/service-airflow-ui.yaml -n airflow-my-namespace

# View everything
kubectl get pods --all-namespaces
kubectl get endpoints --all-namespaces
```

---

## Manifest Locations

### Source of Truth (Git)

```
data_pipeline/
├── airflow/manifests/
│   ├── pv-dags.yaml
│   ├── pvc-dags.yaml
│   ├── pv-airflow-logs.yaml
│   ├── pvc-airflow-logs.yaml
│   ├── pv-output-logs.yaml
│   ├── pvc-output-logs.yaml
│   └── service-airflow-ui.yaml
│
└── dashboard/manifests/
    ├── pod-flask.yaml
    └── service-flask.yaml
```

### Reference Copies on EC2

```
/home/ec2-user/
├── airflow/manifests/          # synced by deploy.sh
└── dashboard/manifests/         # synced by deploy.sh
```

---

## Best Practices

1. **Git is source of truth** — all manifests are version-controlled and applied from the repository
2. **Apply from Mac when possible** — ensures you're using the latest Git version
3. **Use SSH tunnels** — never expose K3S API server (port 6443) to the internet
4. **Check endpoints first** — when a port is unreachable, `kubectl get endpoints` reveals most selector mismatches
5. **Watch pod status** — use `kubectl get pods -w` to monitor deployments in real-time
6. **Save namespace in aliases** — add to `~/.zshrc` or `~/.bash_profile`:
   ```bash
   alias kgp='kubectl get pods --all-namespaces'
   alias kga='kubectl get all --all-namespaces'
   ```

---

## Related Documentation

- `OVERVIEW.md` — architecture and namespace structure
- `DEBUGGING.md` — troubleshooting workflow and common issues
- `deploy.sh` — automated deployment (includes manifest syncing)
