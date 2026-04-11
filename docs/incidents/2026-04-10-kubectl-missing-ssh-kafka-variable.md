# Incident: KAFKA_BOOTSTRAP_SERVERS Set Fails — kubectl exec Missing SSH Wrapper

**Date:** 2026-04-10
**Severity:** Low (deploy partial — Kafka ran fine, only variable-set step failed)

---

## What Happened

`./scripts/deploy.sh` failed at the `Setting Airflow Variable: KAFKA_BOOTSTRAP_SERVERS` step with:

```
The connection to the server 127.0.0.1:6443 was refused - did you specify the right host or port?
```

The deploy section immediately before it (Kafka rollout, topic creation) succeeded normally.

---

## Root Cause

The `kubectl exec` command that sets the Airflow variable was missing the `ssh "$EC2_HOST"` wrapper that every other `kubectl` call in the script uses. Without SSH, the command ran on the local Mac, which has no K3s API server. The Mac's kubectl tried to connect to `127.0.0.1:6443` (its default kubeconfig endpoint) and was refused.

**Broken code (lines 240–241):**
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set KAFKA_BOOTSTRAP_SERVERS kafka.kafka.svc.cluster.local:9092
```

---

## How It Was Identified

The error message `dial tcp 127.0.0.1:6443: connect: connection refused` pointed to a local connection attempt. The Kafka section above it (which used `ssh "$EC2_HOST" "kubectl exec kafka-0 ..."`) succeeded, confirming the pattern: SSH-wrapped calls work, bare calls do not. Comparing the failing block against surrounding blocks revealed the missing `ssh`.

---

## Fix

Wrapped the `kubectl exec` in `ssh "$EC2_HOST"`, matching every other kubectl call in the script:

```bash
# Must run via SSH; kubectl targets EC2's K3s, not local Mac
ssh "$EC2_HOST" "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set KAFKA_BOOTSTRAP_SERVERS kafka.kafka.svc.cluster.local:9092"
```

---

## Why This Fix Works

K3s runs on the EC2 instance. The local Mac's `~/.kube/config` either points to a stale or absent endpoint. SSH routes the `kubectl` command to EC2, where K3s's API server listens on `127.0.0.1:6443` (from EC2's perspective) and the command succeeds.

---

## Prevention

All `kubectl` commands in `deploy.sh` must be wrapped in `ssh "$EC2_HOST" "..."`. When adding new steps that interact with K3s, follow this pattern — never run `kubectl` bare in `deploy.sh`.
