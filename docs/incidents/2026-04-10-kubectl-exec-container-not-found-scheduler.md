# Incident: kubectl exec Fails — "container not found (scheduler)"

**Date:** 2026-04-10  
**Severity:** Deploy blocker (KAFKA_BOOTSTRAP_SERVERS variable not set)

---

## What Caused It

During `./scripts/deploy.sh`, Step 2b2 imports a new `airflow-dbt` image into K3S containerd (~259s). K3S detects the image change and may trigger a rolling restart of the `airflow-scheduler-0` pod. Immediately after, the script tried to `kubectl exec` into that pod to set `KAFKA_BOOTSTRAP_SERVERS` — but the pod was still in its init phase (the `wait-for-airflow-migrations` init container was running). The `scheduler` container inside the pod hadn't started yet.

`kubectl exec` has no built-in retry or readiness awareness — it fires immediately and fails if the target container is not running.

---

## How It Was Encountered and Identified

The error surfaced at this exact line in deploy output:

```
=== Setting Airflow Variable: KAFKA_BOOTSTRAP_SERVERS ===
Defaulted container "scheduler" out of: scheduler, scheduler-log-groomer, wait-for-airflow-migrations (init)
error: Internal error occurred: unable to upgrade connection: container not found ("scheduler")
```

The phrase `container not found ("scheduler")` combined with the init container hint (`wait-for-airflow-migrations (init)`) confirmed the pod was still initializing — not crashed, just not yet past the init phase.

---

## How It Was Fixed

Added a `kubectl wait --for=condition=Ready` gate **before** the `kubectl exec` inside the SSH block in `scripts/deploy.sh`:

```bash
# Before (no readiness gate — races against pod init)
ssh "$EC2_HOST" "kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow variables set KAFKA_BOOTSTRAP_SERVERS kafka.kafka.svc.cluster.local:9092"

# After (waits up to 120s for pod to be fully Ready before exec'ing)
ssh "$EC2_HOST" "
    kubectl wait pod/airflow-scheduler-0 -n airflow-my-namespace --for=condition=Ready --timeout=120s &&
    kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
        airflow variables set KAFKA_BOOTSTRAP_SERVERS kafka.kafka.svc.cluster.local:9092"
```

---

## Why This Fix

`kubectl wait --for=condition=Ready` blocks until all containers in the pod (including init containers) have finished and the main containers are running and passing their readiness probes. This is the idiomatic Kubernetes way to gate exec/port-forward operations on pod readiness — no sleep, no polling, no guessing.

The 120s timeout matches the existing pattern already used later in the deploy script for other Airflow pod waits.

---

## How the Fix Solves the Problem

Previously: exec fired immediately → hit the pod mid-init → `scheduler` container not yet running → error.  
After fix: exec is blocked by `kubectl wait` until the pod is fully Ready → `scheduler` container is guaranteed to be running → exec succeeds → variable is set.
