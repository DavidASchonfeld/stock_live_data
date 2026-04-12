# Incident: Airflow Scheduler Ready Timeout — CPU Starvation During Startup

**Date:** 2026-04-11  
**Severity:** Deploy-blocking (deploy failed after 31 minutes)  
**Status:** Resolved

---

## What Happened

Running `./scripts/deploy.sh` failed at Step 7 (restarting Airflow pods) with:

```
error: timed out waiting for the condition on pods/airflow-scheduler-0
✗ airflow-scheduler-0 Ready FAILED
```

The deploy had been running for 31 minutes and 16 seconds. The dag-processor and triggerer pods became Ready fine. Only the scheduler timed out.

---

## Root Cause

The scheduler's CPU request had been reduced from 200m to 100m in April 2026 to fix a different problem: Kafka was added to the cluster, which pushed total CPU requests on the node to ~97%, causing the scheduler pod to stay in Pending state. Reducing the CPU request to 100m fixed that problem but introduced a new one.

With only 100m guaranteed CPU, Airflow 3.x's startup probe became the bottleneck. Every time Airflow checks whether the scheduler is alive, it runs a command called `airflow jobs check`, which loads the full provider stack (Snowflake, Kafka, etc.). On a t3.large with plenty of CPU available, this takes about 45 seconds. When the scheduler only has 100m CPU guaranteed, it could take much longer — long enough to hit the 60-second probe timeout every single time.

The startup probe was configured with `failureThreshold: 15`, meaning Kubernetes would allow 15 consecutive failures before killing the container and restarting it. With each probe timing out at 60 seconds, those 15 failures consumed 900 seconds (15 minutes). The pod was being killed by Kubernetes at the 15-minute mark, entering CrashLoopBackOff, and the `kubectl wait --timeout=1000s` expired before the pod could recover. Total deploy time of 31m 16s matches exactly a 15-minute wait plus prior deploy steps.

---

## Why Other Pods Were Fine

The dag-processor and triggerer both became Ready within their 600-second windows. These pods are lighter at startup and don't have a startup probe configured at the Helm values level (dag-processor has no startupProbe support at all in the schema). The scheduler is uniquely slow at startup because it runs LocalExecutor tasks — meaning Airflow tasks actually run inside the scheduler pod — and its health checks are heavier as a result.

---

## Fix

Two changes were made, both in April 2026:

**1. CPU rebalance across Airflow pods (`airflow/helm/values.yaml`)**

The scheduler's CPU request was raised back to 200m. To offset this without pushing the node over its allocatable CPU ceiling, three lightly-used pods had their requests reduced by 25m each:

| Pod | Before | After |
|-----|--------|-------|
| scheduler | 100m | 200m |
| webserver | 100m | 75m |
| triggerer | 75m | 50m |
| dagProcessor | 75m | 50m |

Net cluster change: +25m total. The webserver is mostly idle between requests, and the triggerer and dag-processor are lightweight components that burst to their higher limits when briefly needed.

**2. Startup probe safety net (`airflow/helm/values.yaml`)**

The scheduler's `failureThreshold` was raised from 15 to 30. This doubles the startup window from 15 minutes to 30 minutes. With 200m CPU, the scheduler should start in under 5 minutes (a few probe attempts), so this is pure insurance for future high-load deploys. There is no downside: a healthy scheduler passes on its first probe attempt and the extra threshold is never used.

---

## What Was NOT the Issue

- **EBS volume size**: The EC2 instance uses a 100GB gp3 volume with 3,000 baseline IOPS. This is well-provisioned and was not causing slow startup.
- **Terraform configuration**: Terraform correctly created the EBS volume with appropriate size. No infrastructure changes were needed.
- **DAG code**: The DAG files use lazy imports and do not load heavy libraries at parse time. They are not responsible for slow startup.

---

## How to Diagnose This in the Future

If the scheduler times out again, SSH into EC2 and run:

```bash
kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace | tail -50
kubectl logs airflow-scheduler-0 -n airflow-my-namespace --tail=50
```

Look for:
- `Liveness probe failed` or `Startup probe failed` in describe output — indicates probe timeouts
- `OOMKilled` in describe output — indicates memory pressure (different issue)
- `Insufficient cpu` in events — indicates the pod can't be scheduled (CPU requests exceed node capacity)
