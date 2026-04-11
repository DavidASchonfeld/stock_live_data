# Incident: MLflow UI Unreachable via SSH Tunnel

**Date:** 2026-04-10
**Component:** MLflow, scripts/deploy.sh
**Severity:** Medium — MLflow UI completely inaccessible; anomaly detection runs could not be inspected

---

## What Happened

After deploying the MLflow server and running the anomaly detection DAG, the MLflow UI could not be accessed. Opening an SSH tunnel with `ssh -L 5500:localhost:5500 ec2-stock` and then navigating to `http://localhost:5500` in Safari produced:

> "Safari can't open the page 'localhost:5500' because the server unexpectedly dropped the connection."

The MLflow pod and service were both healthy; the problem was entirely in the network path between the browser and the pod.

---

## How It Was Identified

Step 10 of `docs/verification-steps.md` (Access MLflow UI via SSH tunnel) failed with the above error. The error message ("dropped the connection" rather than "could not connect") indicated something was terminating the TCP connection immediately rather than refusing it outright — consistent with nothing listening on the target port.

Inspection of `service-mlflow.yaml` revealed the service type was `ClusterIP`.

---

## Root Cause

Kubernetes `ClusterIP` services are only routable within the cluster's internal network. They do **not** bind to any port on the EC2 host's loopback (`localhost`). The SSH tunnel:

```
local:5500  →  EC2:localhost:5500
```

therefore found nothing listening on `EC2:5500`, and the connection was dropped immediately. This is the expected behavior for a ClusterIP service — it is not a bug in Kubernetes or K3s.

---

## Fix

Added **Step 7d** to `scripts/deploy.sh`. This step SSHes to EC2 and starts `kubectl port-forward` as a persistent background process using `nohup`:

```bash
kubectl port-forward svc/mlflow -n airflow-my-namespace 5500:5500 --address=127.0.0.1
```

This binds `EC2:localhost:5500` and proxies it through K3s's network to the MLflow ClusterIP service. The SSH tunnel then works as-is:

```
browser:5500  →  (SSH tunnel)  →  EC2:localhost:5500  →  (kubectl port-forward)  →  MLflow pod:5500
```

The step also kills any stale port-forward process before starting a new one (idempotent on redeploy), and verifies the process is running after a brief sleep.

Updated the SSH tunnel comment at the end of `deploy.sh` to include `-L 5500:localhost:5500`.

---

## Why This Fix

- **No service type change needed** — ClusterIP is the correct type for an internal service; switching to NodePort would expose the port on all EC2 network interfaces (not just localhost), which is less secure and unnecessary.
- **No Security Group changes needed** — traffic never leaves the EC2 instance; it travels `SSH tunnel → loopback → port-forward → K3s network`.
- **Integrates with existing SSH tunnel pattern** — the project already uses SSH tunnels for Airflow UI (30080) and the dashboard (32147). Adding 5500 is consistent with that approach.
- **Centralized in deploy.sh** — per project convention, all deployment/setup logic lives in `deploy.sh`; the port-forward is started automatically on every deploy.

---

## How to Verify

1. Run `./scripts/deploy.sh`
2. Confirm Step 7d output: `port-forward running OK`
3. Open SSH tunnel: `ssh -L 5500:localhost:5500 ec2-stock`
4. Navigate to `http://localhost:5500` — MLflow UI should load
5. Confirm `anomaly_detection` experiment is visible with runs, logged metrics, and the `isolation_forest` artifact
