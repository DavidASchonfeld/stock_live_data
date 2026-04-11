# Incident: kubectl Missing SSH Wrapper — MLFLOW_TRACKING_URI Variable Set

**Date:** 2026-04-10
**Severity:** Deploy blocker (Step 7 exit, variable never written)

---

## What Happened

`deploy.sh` failed immediately after printing:

```
=== Setting Airflow Variable: MLFLOW_TRACKING_URI ===
```

with four repeated errors:

```
E0410 18:05:15 memcache.go:265 "Unhandled Error" err="couldn't get current server API group list: Get \"https://127.0.0.1:6443/api?timeout=32s\": dial tcp 127.0.0.1:6443: connect: connection refused"
The connection to the server 127.0.0.1:6443 was refused - did you specify the right host or port?
```

The Airflow variable `MLFLOW_TRACKING_URI` was never set, meaning `anomaly_detector.py` would fail to reach MLflow at runtime.

---

## Root Cause

Every `kubectl` call in `deploy.sh` is wrapped in `ssh "$EC2_HOST" "..."` because the K3S cluster lives on the remote EC2 node — the local machine has no kubeconfig for it. One command was written without the SSH wrapper:

```bash
# BROKEN — runs kubectl locally, not on EC2
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set MLFLOW_TRACKING_URI http://mlflow.airflow-my-namespace.svc.cluster.local:5500
```

The local `kubectl` tried to reach `127.0.0.1:6443` (the default kubeconfig endpoint), which doesn't exist on the dev machine.

---

## How It Was Identified

The error message `dial tcp 127.0.0.1:6443: connect: connection refused` is the giveaway: the local machine has no K3S cluster, so any `kubectl` call without SSH will always hit `127.0.0.1`. Comparing the failing lines against the surrounding Step 7b/7c blocks confirmed the SSH wrapper was missing.

---

## Fix

Wrapped the command in `ssh "$EC2_HOST"` to match every other `kubectl` call in the script:

```bash
# FIXED — routes kubectl to EC2 via SSH
ssh "$EC2_HOST" "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set MLFLOW_TRACKING_URI http://mlflow.airflow-my-namespace.svc.cluster.local:5500"
```

**Why this fix:** It's the established pattern throughout the script. The cluster is remote-only; no local kubeconfig exists or should be created. SSH is the single authoritative path to the cluster.

**Why this actually solved it:** `kubectl` now executes on the EC2 host, which has the correct `~/.kube/config` pointing to the local K3S API server (`127.0.0.1:6443` from EC2's perspective, not the dev machine's).
