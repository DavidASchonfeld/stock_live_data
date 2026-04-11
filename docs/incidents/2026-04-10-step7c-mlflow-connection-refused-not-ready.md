# Incident: Step 7c — MLflow Connection Refused (Pod Not Ready)

**Date:** 2026-04-10
**Step:** 7c — Resetting MLflow experiment artifact root
**Error:** `ConnectionRefusedError: [Errno 111]` → `mlflow.airflow-my-namespace.svc.cluster.local:5500`

---

## What Caused It

Step 7c opens a Python shell inside the Airflow scheduler pod and calls `client.search_experiments()` via MLflow's HTTP API. This requires the MLflow pod to already be listening on port 5500. However, the MLflow deployment's readiness probe has an initial delay of 10s and up to 6 × 5s retry periods — meaning the pod can take up to ~40s after starting before its HTTP server is ready. Step 7c ran before that window closed.

## How It Was Encountered and Identified

The deploy script printed the full Python traceback originating from `<stdin>` inside the scheduler pod:

```
urllib3.exceptions.NewConnectionError: ... Failed to establish a new connection: [Errno 111] Connection refused
```

The target host `mlflow.airflow-my-namespace.svc.cluster.local:5500` confirmed the K8s service was correct — the pod existed but wasn't ready yet. The `kubectl exec` into the scheduler pod succeeded (pod was running), ruling out a scheduler problem. The connection was refused at the MLflow pod itself.

## How It Was Fixed

Added `kubectl rollout status deployment/mlflow -n airflow-my-namespace --timeout=120s` immediately before the Python heredoc in Step 7c of `scripts/deploy.sh`.

```bash
ssh "$EC2_HOST" "kubectl rollout status deployment/mlflow -n airflow-my-namespace --timeout=120s"
```

## Why This Fix Was Chosen

`kubectl rollout status` blocks until all replicas in the deployment pass their readiness probe (HTTP GET `/health` on port 5500). This is the canonical Kubernetes way to wait for a deployment — it ties directly to the same readiness signal that K8s uses for traffic routing, so by the time the command returns, port 5500 is guaranteed to be listening.

## How the Fix Solves the Problem

Before the fix, the Python block ran immediately after the MLflow manifests were applied, racing against the pod's startup time. After the fix, the script blocks at `rollout status` until the readiness probe passes, then runs the Python block — at which point the HTTP server is confirmed ready and `search_experiments()` succeeds.
