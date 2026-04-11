# MLflow Pod Stuck Pending: Node Taint Blocked Scheduling

**Date:** 2026-04-10
**Severity:** Deploy blocker — MLflow pod never reached Running

---

## What Happened

During `./scripts/deploy.sh`, Step 2b6 (MLflow rollout) timed out. The new pod was stuck in `Pending` with no scheduling progress:

```
0/1 nodes are available: 1 node(s) had untolerated taint(s). no new claims to deallocate
```

The deploy printed `ERROR: MLflow rollout timed out` and exited non-zero.

---

## Root Cause

The K3s node (`ip-172-31-81-4`) had a **transient pressure taint** active during the deploy window:

```
node.kubernetes.io/disk-pressure:NoSchedule
```

Kubernetes automatically posts this taint when the node hits a resource pressure threshold (low disk, low memory, etc.). It prevents new pods from scheduling onto the node — a safety mechanism to avoid making a stressed node worse.

The problem was compounded by the `Recreate` rollout strategy in `deployment-mlflow.yaml`. `Recreate` terminates the old pod **before** starting the new one (required for the `ReadWriteOnce` PVC). This meant:

1. The old MLflow pod was deleted.
2. The new pod tried to schedule exactly when the taint was active.
3. The new pod sat in `Pending` with no node to land on.
4. Even after the taint cleared naturally, the pod remained stuck until the deploy timed out.

---

## How It Was Identified

`kubectl describe pod` (printed by the deploy script's diagnostic block) showed the scheduler event with `had untolerated taint(s)`. Running `kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints'` confirmed the taint was already gone by the time we investigated — it had cleared on its own.

---

## Fix

**Immediate:** Waited for the transient taint to clear, then reran `./scripts/deploy.sh`. The pod came up cleanly.

**Preventive — `scripts/deploy.sh`** — added a node condition diagnostic block immediately before the Step 2b6 `kubectl apply` block:
```bash
# Print node taints and pressure conditions before deploy — catches scheduling blockers early
ssh "$EC2_HOST" "
    echo '--- Node taints and pressure conditions pre-MLflow-rollout ---'
    kubectl get nodes -o custom-columns='NAME:.metadata.name,TAINTS:.spec.taints'
    kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}: {range .status.conditions[*]}{.type}={.status}  {end}{\"\n\"}{end}'
"
```

---

## Why Tolerations Were Rejected

The obvious fix would be to add `tolerations` for disk/memory pressure taints to `deployment-mlflow.yaml` so the pod can schedule through them. This was intentionally rejected:

- Pressure taints are Kubernetes' safety system — they signal that the node is genuinely under resource stress.
- Bypassing them would schedule MLflow onto a node that may immediately OOMKill it or trigger cascading evictions of other pods.
- The correct response to a pressure taint is to fix the underlying resource problem (e.g., the ephemeral storage eviction fix documented in `2026-04-10-mlflow-ephemeral-storage-eviction.md`), not to ignore the signal.

---

## Why This Fix Works

The diagnostic block is read-only and runs before manifests are applied. If a pressure taint is active, it will now be visible in the deploy log at the moment the operator can still abort (`Ctrl+C`) and investigate before the `Recreate` strategy deletes the running pod.
