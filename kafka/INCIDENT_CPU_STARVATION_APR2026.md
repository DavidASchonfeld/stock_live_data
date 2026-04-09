# Incident: Airflow Scheduler Pending After Kafka Deployment (Apr 2026)

## How It Was Encountered

While verifying Step 5 of `KAFKA_SETUP_NOTES.md` (confirming Airflow can reach Kafka
cross-namespace), the `nc` connectivity check failed with an unexpected error:

```bash
ssh ec2-stock "
    kubectl exec -n airflow-my-namespace \
        \$(kubectl get pod -n airflow-my-namespace -l component=scheduler -o jsonpath='{.items[0].metadata.name}') \
        -- nc -zv kafka.kafka.svc.cluster.local 9092
"
# Output:
# Error from server (BadRequest): pod airflow-scheduler-0 does not have a host assigned
```

This error means the pod has no node assigned — it is `Pending`, not running — so `kubectl exec`
has nowhere to connect to. The nc test itself was never the problem.

## How It Was Identified

**Step 1 — confirmed the pod was Pending:**
```bash
ssh ec2-stock "kubectl get pod -n airflow-my-namespace -l component=scheduler"
# NAME                  READY   STATUS    RESTARTS   AGE
# airflow-scheduler-0   0/2     Pending   0          26m
```

**Step 2 — read the scheduler's events to find the root cause:**
```bash
ssh ec2-stock "kubectl describe pod -n airflow-my-namespace -l component=scheduler"
# ...
# Events:
#   Warning  FailedScheduling  26m  default-scheduler
#     0/1 nodes are available: 1 Insufficient cpu.
```

**Step 3 — checked actual node CPU consumption:**
```bash
ssh ec2-stock "kubectl describe nodes | grep -A5 'Allocated resources'"
# Allocated resources:
#   Resource  Requests      Limits
#   cpu       1950m (97%)   3800m (190%)
#   memory    3084Mi (39%)  6826Mi (87%)
```

Node at 97% CPU requests with only 50m free. The scheduler pod needed 200m — it would never fit.

## What Was Causing It

Kubernetes uses **CPU requests** as scheduling guarantees. Before placing a pod on a node, the
scheduler checks: "does this node have enough *unreserved* CPU to promise this pod its requested
amount?" If not, the pod stays `Pending` indefinitely.

The node had **2000m total CPU** (2 vCPUs on a t3.large). After Kafka was deployed it consumed
an additional 100m, pushing the node to **1950m reserved (97%)**. Only 50m remained free.

The Airflow scheduler pod requested **200m CPU** — it could not fit in the remaining 50m, so
Kubernetes refused to schedule it.

## The Fix

Reduced CPU **requests** (not limits) for all Airflow components in `airflow/helm/values.yaml`:

| Component    | Before | After | Saved |
|--------------|--------|-------|-------|
| scheduler    | 200m   | 100m  | 100m  |
| webserver    | 200m   | 100m  | 100m  |
| apiServer    | 100m   | 75m   | 25m   |
| triggerer    | 100m   | 75m   | 25m   |
| dagProcessor | 100m   | 75m   | 25m   |
| **Total**    |        |       | **275m** |

After the fix: ~1675m reserved with all pods running (~84%), leaving ~325m headroom.
Deployed via `./scripts/deploy.sh` (Step 2b syncs values.yaml; Step 2d runs `helm upgrade`).

## Why This Fix Was Chosen

Three options existed:

| Option | What it does | Cost | Downside |
|--------|-------------|------|----------|
| **Reduce CPU requests** (chosen) | Lowers the scheduling promise, not the actual ceiling — pods can still burst to their existing limits | Free | Slightly less scheduling guarantee (fine for dev/portfolio) |
| Upgrade EC2 instance type | More vCPUs (e.g. t3.large → t3.xlarge: 2→4 vCPUs) | ~$50–70/mo more | Ongoing cost |
| Add a second EC2 worker node | More nodes = more schedulable capacity | Even more cost | More complex to manage |

**Why reducing requests is right for this project:**

CPU requests and CPU limits are different things. Requests are the scheduler's *reservation
system* — they determine whether a pod can be placed on a node. Limits are the *hard ceiling* —
they cap actual CPU usage at runtime. Reducing requests does not slow anything down; the pods
can still burst to their existing limits (1000m for scheduler, etc.) whenever they need it.

The original requests were set conservatively when the cluster had more headroom. Adding Kafka
was the trigger that exposed the over-provisioning. For a single-node dev/portfolio cluster
running several components, right-sizing requests to realistic idle baselines (not burst peaks)
is the correct approach — and it's free.

Adding EBS (Elastic Block Storage) would **not** have helped — EBS is disk storage, not compute.
This was a CPU reservation problem, unrelated to disk capacity or I/O.

## Verifying the Fix

Run these after `./scripts/deploy.sh` completes (see timing note in the script's Step 7 output).

**Test 1 — scheduler pod is Running:**
```bash
ssh ec2-stock "kubectl get pod -n airflow-my-namespace -l component=scheduler"
# Expected: STATUS=Running, READY=2/2
```

**Test 2 — node CPU headroom is healthy:**
```bash
ssh ec2-stock "kubectl describe nodes | grep -A5 'Allocated resources'"
# Expected: cpu requests well below 90% (target ~84% with all pods running)
```

**Test 3 — Airflow can reach Kafka (the original Step 5 from KAFKA_SETUP_NOTES.md):**
```bash
ssh ec2-stock "
    kubectl exec -n airflow-my-namespace \
        \$(kubectl get pod -n airflow-my-namespace -l component=scheduler -o jsonpath='{.items[0].metadata.name}') \
        -- nc -zv kafka.kafka.svc.cluster.local 9092
"
# Expected: Connection to kafka.kafka.svc.cluster.local 9092 port [tcp/*] succeeded!
```

All three passing confirms the scheduler is scheduled, the node has headroom, and cross-namespace
Kafka connectivity works. You can then continue with Step 6+ of `KAFKA_SETUP_NOTES.md`.
