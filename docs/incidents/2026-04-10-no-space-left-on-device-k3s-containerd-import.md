# Incident: No Space Left on Device — K3S Containerd Import of airflow-dbt Image

**Date:** 2026-04-10  
**Severity:** Deploy blocker  
**Status:** Resolved

---

## What Happened

`./scripts/deploy.sh` failed at Step 2b2 with:

```
ctr: failed to ingest "blobs/sha256/...": failed to copy:
write /var/lib/rancher/k3s/agent/containerd/io.containerd.content.v1.content/ingest/.../data:
no space left on device
```

The Docker build completed successfully. The failure was on the `docker save | sudo k3s ctr images import -` line that imports the newly built image into K3S containerd.

---

## Root Cause

Two compounding issues:

### 1. K3S containerd GC never ran between deploys

`sudo k3s ctr images rm` removes only the image **manifest reference** from the K3S image index. The underlying content blobs (the actual layer data) remain on disk in `/var/lib/rancher/k3s/agent/containerd/io.containerd.content.v1.content/` until a garbage collection pass runs. The deploy script purged K3S image references but never triggered GC, so orphaned blobs accumulated across every deploy until the content store ran out of space.

### 2. Old Docker airflow-dbt images not cleaned up

Each deploy creates a new timestamped Docker image (`airflow-dbt:3.1.8-dbt-YYYYMMDDHHMMSS`). The old images were never removed from Docker's image store, consuming additional disk space on the EC2 instance. The MLflow import step (2b5a) already ran `docker image prune -f` before its import — the airflow-dbt step was inconsistently missing this.

---

## How It Was Identified

The error message pointed directly to the K3S containerd ingest path running out of space. Comparing Step 2b2 (airflow-dbt) against Step 2b5a (MLflow) revealed that MLflow's step ran both `k3s ctr images rm` and `docker image prune -f` before importing, while airflow-dbt's step only ran `k3s ctr images rm` — no GC, no Docker prune.

---

## Fix

Added three lines to Step 2b2 in `scripts/deploy.sh`, between the K3S purge and the import:

1. **`sudo k3s ctr content gc`** — triggers containerd's garbage collector, which walks all namespaces and frees any blobs no longer referenced by an image manifest. This is what actually reclaims the disk space that `images rm` only unlinked.

2. **`docker images | grep 'airflow-dbt' | grep -v '$BUILD_TAG' | xargs -r docker rmi`** — removes all previous timestamped airflow-dbt images from Docker's store, keeping only the one just built.

3. **`docker image prune -f`** — removes any dangling Docker image layers (unreferenced intermediate layers from the build). Mirrors what the MLflow step already did.

---

## Why This Fix Works

- `k3s ctr content gc` walks the K3S containerd content store and deletes every blob that has no live reference (i.e., no image manifest pointing at it). After `images rm` unlinks the old manifests, GC is what physically reclaims the bytes.
- Removing old Docker `airflow-dbt:*` images frees host disk (Docker and K3S share the same underlying filesystem).
- All three cleanup commands use `|| true` so a failure (e.g., no images to remove) does not abort the deploy.
