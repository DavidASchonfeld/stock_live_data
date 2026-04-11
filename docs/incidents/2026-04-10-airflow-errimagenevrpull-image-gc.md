# Incident: Init:ErrImageNeverPull — K3S Image GC Evicts Airflow Image Mid-Deploy

**Date:** 2026-04-10  
**Severity:** Deploy blocker (Airflow pods could not start; ml-venv and MLflow reset also failed as cascading failures)

---

## What Caused It

The deploy script imports the custom `airflow-dbt` image (~3.3 GiB) into K3S containerd early in the deploy (Step 2b2), then imports the MLflow image (~1.1 GiB) roughly 10 minutes later (Step 2b5a). The combined 4.4 GiB import pushed disk utilization past K3S's image GC high-watermark (~85%). 

Meanwhile, the Helm upgrade (Step 2d) immediately created new api-server pods using the freshly imported image. Those pods started, ran briefly, then crashed with `Error` status (a pre-existing api-server startup issue). Once the last container using the image exited, the image became "unused." K3S's image GC considers any image unused for longer than `imageMinimumGCAge` (default: 2 minutes) eligible for eviction. Under disk pressure, it evicted `airflow-dbt:3.1.8-dbt-<TIMESTAMP>`, reclaiming 3.3 GiB.

When Step 7 ran ~20 minutes after the original import and tried to restart the scheduler, dag-processor, and triggerer pods, the image was gone. Because `imagePullPolicy: Never` is set in `values.yaml` (to avoid ECR costs — the image is built and imported locally), Kubernetes cannot re-pull the image and surfaces `ErrImageNeverPull`.

---

## How It Was Encountered and Identified

The deploy log showed:

```
error: timed out waiting for the condition on pods/airflow-scheduler-0
WARNING: Airflow pod restart or DAG verification failed.
...
airflow-scheduler-0   0/2   Init:ErrImageNeverPull   0   2m17s
```

Steps 7b and 7c then failed with `container not found ("scheduler")` because the scheduler pod was stuck in the init phase and never became Ready.

The `Init:` prefix in `Init:ErrImageNeverPull` is the key: it means an **init container** (specifically `wait-for-airflow-migrations`, which uses the same `airflow-dbt` image) cannot find the image locally with `pullPolicy: Never`.

The GC theory was confirmed by looking at the api-server pod age progression in the pod listing:
- `s6vq6` (112m old, `Error`) — found the image, started, then crashed
- `rfbwp` (63m old, `Init:ContainerStatusUnknown`) — image partially evicted
- `dphz7` (24m old, `Init:ErrImageNeverPull`) — image fully gone

The image was present immediately after import (`k3s ctr images list` verified it), but absent by the time Step 7 ran.

---

## How It Was Fixed

Added **Step 7a** to `scripts/deploy.sh` — a check-and-reimport guard inserted between the Flask pod restart (Step 6) and the Airflow pod restarts (Step 7):

```bash
echo "=== Step 7a: Ensuring airflow image is still in K3S containerd ==="
ssh "$EC2_HOST" "
    if sudo k3s ctr images list | grep -q 'airflow-dbt:$BUILD_TAG'; then
        echo 'airflow-dbt:$BUILD_TAG confirmed present in K3S containerd'
    else
        echo 'Re-importing from Docker store...'
        docker save airflow-dbt:$BUILD_TAG | sudo k3s ctr images import -
    fi
"
```

Also increased the pod readiness timeout in Step 7 from `--timeout=120s` to `--timeout=360s`. The scheduler's `startupProbe` budget is 10 × 30s = 300s; 120s was too short for a healthy deploy and triggered false warnings.

---

## Why This Fix Was Chosen

The image is always still in Docker's store (the purge in Step 2b2 only removes OLD tags from K3S containerd, not from Docker). The re-import on a warm Docker layer cache is fast (seconds for metadata, minutes only if layers were also evicted). 

The check is O(1) — if the image is present, it's a single grep and the step completes in under a second. The re-import only runs when actually needed (rare, under disk pressure). This is the minimum-complexity fix that breaks the failure chain without disrupting the rest of the deploy sequence.

---

## How the Fix Actually Solves the Problem

By running Step 7a immediately before Step 7:
1. The image is guaranteed to be in K3S containerd at the moment pods are restarted
2. The GC eviction window shrinks from ~20 minutes to ~1 second (the gap between Step 7a and Step 7)
3. Even if GC runs between Step 7a and Step 7, the image was just re-imported and GC will not evict it that quickly
4. Pods restarted in Step 7 find the image, init containers start, and the scheduler reaches Ready within the new 360s budget
5. Steps 7b (ml-venv) and 7c (MLflow reset) proceed normally once the scheduler is Ready
