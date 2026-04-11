# MLflow Pod Evicted: Ephemeral Storage Exhaustion

**Date:** 2026-04-10
**Severity:** Deploy blocker — MLflow pod never reached Running

---

## What Happened

During `./scripts/deploy.sh`, Step 2b6 (MLflow rollout) timed out. The pod was stuck in `Pending` after being evicted:

```
Warning  Evicted  kubelet  The node was low on resource: ephemeral-storage.
Threshold quantity: 2544205452, available: 2337332Ki.
Container mlflow was using 1856Ki, request is 0, has larger consumption of ephemeral-storage.
```

The deploy printed `ERROR: MLflow rollout timed out` and exited non-zero.

---

## Root Cause

Two compounding problems:

**1. Node ephemeral storage was full (~2.3 GB available < ~2.4 GB eviction threshold)**

Every deploy imports ~1.1 GB of MLflow image layers into K3S containerd (`/var/lib/rancher/k3s/agent/containerd/`). Unlike the `airflow-dbt` image (which was already being purged before re-import), the old MLflow K3S image was never removed — it just accumulated across deploys. Dangling Docker images from `--no-cache` builds added further pressure on `/var/lib/docker/`.

**2. MLflow had no `ephemeral-storage` request set (`request is 0`)**

Kubernetes eviction priority: kubelet evicts `BestEffort` pods first (no requests/limits), then `Burstable` (request < limit), then `Guaranteed` (request == limit). With `ephemeral-storage` request absent, the MLflow pod registered as having zero ephemeral cost — kubelet evicted it immediately when the node hit the storage threshold, even though the container was only using 1856 Ki.

---

## How It Was Identified

The deploy script's diagnostic block (`kubectl describe pod`) showed the `Evicted` event with the reason `low on resource: ephemeral-storage` and `request is 0`. Comparing to the `airflow-dbt` step confirmed that MLflow was the only image without pre-import cleanup.

---

## Fix

**`airflow/manifests/mlflow/deployment-mlflow.yaml`** — added `ephemeral-storage` request and limit:
```yaml
resources:
  requests:
    ephemeral-storage: "100Mi"   # raises eviction priority above request=0 pods
  limits:
    ephemeral-storage: "500Mi"   # caps container ephemeral usage (logs, tmp)
```

**`scripts/deploy.sh`** — added cleanup before MLflow image import (Step 2b5a):
```bash
sudo k3s ctr images ls | grep 'mlflow' | awk '{print $1}' | xargs -r sudo k3s ctr images rm 2>/dev/null || true
docker image prune -f || true
```

---

## Why This Fix Works

- Removing the old MLflow K3S image before re-importing frees ~1.1 GB of ephemeral storage on every deploy, keeping the node below the eviction threshold.
- `docker image prune -f` removes dangling images from the Docker store (accumulated from repeated `--no-cache` builds), recovering additional disk space.
- Setting `ephemeral-storage: 100Mi` request means kubelet properly accounts for the pod's storage footprint. Even under future storage pressure, it will now only be evicted after pods with lower or zero requests.
