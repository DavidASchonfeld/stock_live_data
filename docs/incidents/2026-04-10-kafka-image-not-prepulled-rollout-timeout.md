# Kafka Rollout Timeout — Image Not Pre-Pulled into K3s Containerd

**Date:** 2026-04-10
**Severity:** Medium — Kafka pod never became Ready; topic creation skipped; Airflow consumer DAGs unable to connect until next deploy

---

## What Happened

`./scripts/deploy.sh` Step 2b4 printed:

```
Waiting for 1 pods to be ready...
error: timed out waiting for the condition
WARNING: Kafka rollout did not complete — skipping topic creation. Run deploy again once it is running.
```

The pod `kafka-0` never reached Ready state within the 480-second rollout timeout.

---

## How It Was Encountered and Identified

The deadlock-detection logic in Step 2b4 output:

```
No deadlock (currentRevision=kafka-79f77f7c9f, updateRevision=kafka-79f77f7c9f, podReady=).
```

`podReady=` being **empty** (not `True` or `False`) indicated the pod had no `Ready` condition yet — consistent with the pod being in `ContainerCreating` or `Pending` state at the time of the check. This meant the pod had not even started its container.

Reviewing `scripts/deploy.sh`, every other image that runs in K3s has an explicit pre-pull step:
- **airflow-dbt**: `docker build` → `docker save | k3s ctr images import`
- **MLflow**: `docker pull | docker save | k3s ctr images import`
- **Kafka**: *nothing* — relied on K3s pulling `docker.io/apache/kafka:4.0.0` at runtime (`imagePullPolicy: IfNotPresent`)

On the same day, a separate MLflow ephemeral storage eviction incident caused K3s to evict pod storage. This likely also cleared the K3s containerd image cache. A cold pull of the ~500 MB Kafka image at pod-scheduling time consumed most (or all) of the 480-second rollout window before the startup probe could even begin, causing the timeout.

---

## Root Cause

The Kafka image was not pre-loaded into K3s containerd before the StatefulSet was applied. Unlike airflow-dbt and MLflow, there was no explicit pre-pull step. After the image cache was cleared by the storage eviction, K3s needed to pull the full image at pod startup, which exhausted the rollout timeout.

---

## Fix

Added **Step 2b3a** to `scripts/deploy.sh` between the Kafka manifest sync (2b3) and the Kafka deploy (2b4):

```bash
echo "=== Step 2b3a: Pre-pulling Kafka image into K3s containerd ==="
ssh "$EC2_HOST" "
    sudo k3s crictl pull docker.io/apache/kafka:4.0.0 \
    && echo 'Kafka image ready in K3s containerd.'
"
```

`crictl pull` is idempotent — it is a no-op if the image is already cached, and pulls only when missing. This separates image availability from the rollout timeout, so the 480-second window is consumed only by actual pod startup and the KRaft initialization startup probe.

---

## Why This Fix

- `crictl pull` is the correct K3s-native way to warm the containerd cache for a public image without routing through Docker
- Mirrors the intent of the existing airflow-dbt and MLflow pre-pull steps
- Idempotent — adds negligible overhead on subsequent deploys when image is cached
- Keeps `imagePullPolicy: IfNotPresent` in the manifest (no change to kafka.yaml needed)
