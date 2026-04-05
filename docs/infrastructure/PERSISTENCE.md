# Persistent Storage Deep Dive

How PersistentVolumes and PersistentVolumeClaims work in this project, what can go wrong, and the hidden complexity behind `hostPath` volumes on a single-node K3s cluster.

**Navigation:**
- General K3s risks? → [K3S_RISKS.md](K3S_RISKS.md)
- PV path mismatch failure mode? → [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md#k8-1-pvpvc-path-mismatch)
- System architecture? → [../architecture/SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md)

---

## Your PV/PVC Layout

```
EC2 Host Filesystem
├── /home/ubuntu/airflow/dags/     ← deploy.sh syncs DAG files here
│   ├── dag_stocks.py
│   ├── dag_weather.py
│   ├── stock_client.py
│   └── ...
│
├── /opt/airflow/logs/               ← Airflow task logs
│
└── /opt/airflow/out/                ← Custom output logs (file_logger.py)


K8s PersistentVolumes (host paths on EC2)
├── dag-pv          → hostPath: /home/ubuntu/airflow/dags/
├── airflow-logs-pv → hostPath: /opt/airflow/logs/
└── output-logs-pv  → hostPath: /opt/airflow/out/


K8s PersistentVolumeClaims (in airflow-my-namespace)
├── dag-pvc          → binds to dag-pv
├── airflow-logs-pvc → binds to airflow-logs-pv
└── output-logs-pvc  → binds to output-logs-pv


Pod Mounts
Airflow Scheduler Pod:
  /opt/airflow/dags/  ← mounted from dag-pvc
  /opt/airflow/logs/  ← mounted from airflow-logs-pvc
  /opt/airflow/out/   ← mounted from output-logs-pvc
```

---

## How hostPath Volumes Work

`hostPath` is the simplest PV type — it maps a directory on the EC2 host directly into the pod.

```yaml
# PV definition (pv-dags.yaml)
apiVersion: v1
kind: PersistentVolume
metadata:
  name: dag-pv
spec:
  capacity:
    storage: 5Gi
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: /home/ubuntu/airflow/dags/   # ← this folder on EC2
  storageClassName: ""
  claimRef:
    namespace: airflow-my-namespace
    name: dag-pvc
```

**What actually happens at mount time:**

1. Pod starts and requests `dag-pvc`
2. K8s finds `dag-pvc` is bound to `dag-pv`
3. `dag-pv` says `hostPath: /home/ubuntu/airflow/dags/`
4. The kubelet creates a bind mount from that host directory into the pod's filesystem
5. Pod sees files at `/opt/airflow/dags/` (its mount point) — these ARE the files at `/home/ubuntu/airflow/dags/` on the host

**No copying happens.** The pod reads and writes directly to the host directory. Changes are immediate and bidirectional.

---

## The Five Hidden Risks of hostPath

### Risk 1: Silent Path Mismatch (You've Hit This)

K8s does **not validate** that the `hostPath` directory exists or contains expected files. If the path is wrong:

```
PV says:       hostPath: /tmp/airflow-dags/           (old path, empty)
deploy.sh to:  /home/ubuntu/airflow/dags/           (correct path, has files)
Pod sees:      /opt/airflow/dags/ → empty directory    (mounted the wrong path)
```

**No error anywhere.** The PV binds, the PVC claims, the pod mounts, and you get an empty directory. You have to manually compare paths to find the problem.

**Prevention:** Add a validation step to `deploy.sh`:
```bash
# Read PV hostPath from manifest
PV_PATH=$(grep "path:" airflow/manifests/pv-dags.yaml | awk '{print $2}')
# Compare to deploy target
if [ "$PV_PATH" != "$EC2_DAG_PATH" ]; then
  echo "ERROR: PV hostPath ($PV_PATH) doesn't match deploy target ($EC2_DAG_PATH)"
  exit 1
fi
```

### Risk 2: Filesystem Cache Staleness (You've Hit This)

When multiple pods mount the same `hostPath`, each pod gets its own filesystem cache (dentry/inode cache at the Linux kernel level). File changes made by one process may not be visible to another for seconds to minutes.

**The mechanism:**

```
1. rsync writes new dag_stocks.py to /home/ubuntu/airflow/dags/
2. EC2 filesystem assigns new inode (e.g., 84268967)
3. Scheduler pod's kernel cache refreshes → sees new inode → finds file
4. Processor pod's kernel cache is stale → sees old inode list → file missing
5. Airflow sync cycle asks Processor: "does dag_stocks.py exist?"
6. Processor says "no" (stale cache) → DAG marked is_stale: True
7. After cache expires (varies: 30s-5min) → Processor finally sees the file
```

**Why this happens on hostPath but not on network filesystems:** Network filesystems (NFS, EFS) handle cache coherency across clients. `hostPath` is a raw bind mount — the kernel treats each mount point as an independent accessor with its own dentry cache.

**Prevention:** Restart all pods that read from the volume after deploying new files.

### Risk 3: The Reclaim Policy Trap

PVs have a `persistentVolumeReclaimPolicy`:

| Policy | What happens when PVC is deleted | Risk |
|--------|----------------------------------|------|
| **Retain** (default) | PV keeps data, status becomes `Released` | PV can't be re-bound. New PVC won't claim it. Must manually delete and recreate PV. |
| **Delete** | PV and data are deleted | Data loss if you accidentally delete the PVC |
| **Recycle** | PV data wiped, PV becomes `Available` | Deprecated. Don't use. |

**Your current policy:** `Retain` (the default).

**The trap:** If you delete a PVC (e.g., during troubleshooting), the PV goes to `Released` state. Creating a new PVC with the same name does NOT automatically re-bind to the Released PV. You must:

1. Delete the PVC
2. Delete the PV (or remove `claimRef` from PV spec)
3. Recreate PV
4. Recreate PVC
5. Restart pods

This is a 5-step process for what seems like it should be automatic. Document it as a runbook.

### Risk 4: No Capacity Enforcement

`hostPath` PVs declare a `capacity` (e.g., `5Gi`), but **K8s does not enforce it**. The pod can write until the host filesystem is full. The capacity field is purely informational — it's used for PVC matching, not for actual enforcement.

**What this means:**
- A DAG that logs excessively can fill the host disk
- MariaDB can grow beyond its declared PV capacity
- When the host disk fills, ALL pods fail (not just the one that caused it)

**Prevention:** Monitor host disk usage (`df -h`). Set up log rotation. The `capacity` field is a lie on `hostPath` — trust `df`, not `kubectl get pv`.

### Risk 5: No Backup or Snapshot

`hostPath` volumes don't integrate with K8s volume snapshot APIs. Unlike EBS CSI or cloud-native storage:

- No `kubectl` command to snapshot a PV
- No automated backup integration
- Data lives on a single EBS volume — if the volume corrupts, data is gone

**For MariaDB data**, this means your database has no automated backup. If the EBS volume fails or data corrupts, you lose everything.

**Prevention for portfolio project:**
- Periodic `mysqldump` exported to S3 (even monthly is better than nothing)
- EBS snapshots via AWS Console or CLI
- Document that production would use managed database (RDS) or proper backup automation

---

## PV/PVC Lifecycle States

Understanding states helps diagnose binding issues:

```
PV States:
  Available  → PV exists, no PVC has claimed it
  Bound      → PV is claimed by a PVC (normal operating state)
  Released   → PVC was deleted, PV retains data but won't auto-rebind
  Failed     → PV reclamation failed

PVC States:
  Pending    → PVC created but no matching PV found
  Bound      → PVC is bound to a PV (normal operating state)
  Lost       → PV that was bound to this PVC no longer exists
```

### Common State Problems

**PVC stuck in `Pending`:**
```bash
kubectl describe pvc dag-pvc -n airflow-my-namespace
# Look at Events section for why it can't bind
```
Common causes:
- No PV with matching `storageClassName`
- PV capacity smaller than PVC request
- PV's `claimRef` points to a different PVC
- PV is in `Released` state (from previous PVC deletion)

**PV stuck in `Released`:**
```bash
# Option 1: Delete and recreate PV + PVC
kubectl delete pv dag-pv
kubectl apply -f airflow/manifests/pv-dags.yaml
kubectl apply -f airflow/manifests/pvc-dags.yaml

# Option 2: Clear claimRef to make PV Available again
kubectl patch pv dag-pv --type json -p '[{"op":"remove","path":"/spec/claimRef"}]'
# Then recreate PVC
```

---

## PV Debugging Cheat Sheet

```bash
# See all PVs and their states
kubectl get pv

# See all PVCs and their states
kubectl get pvc -A

# See which PV a PVC is bound to
kubectl get pvc dag-pvc -n airflow-my-namespace -o jsonpath='{.spec.volumeName}'

# See the host path a PV points to
kubectl get pv dag-pv -o jsonpath='{.spec.hostPath.path}'

# Compare: what does deploy.sh sync to?
grep "EC2_DAG_PATH" scripts/deploy.sh

# Are the files actually there on EC2?
ssh ec2-stock ls -la /home/ubuntu/airflow/dags/

# Are they visible inside the pod?
ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls -la /opt/airflow/dags/

# Full PV details (events, conditions, etc.)
kubectl describe pv dag-pv
```

---

## When to Move Beyond hostPath

`hostPath` is appropriate for your current setup (single-node, portfolio project). Consider migrating when:

| Trigger | Move to |
|---------|---------|
| Adding a second K3s node | Local-path-provisioner (K3s built-in) or NFS |
| Need automated backups | EBS CSI driver (snapshots via K8s API) |
| Moving to production | EFS (shared across nodes) or managed database (RDS) |
| Snowflake migration | Remove MariaDB PV entirely — data lives in Snowflake |

**For your Snowflake/dbt migration plan:** The MariaDB PV becomes unnecessary. DAG files PV could migrate to a ConfigMap or Git-sync sidecar. This simplifies your storage story significantly.

---

**Last updated:** 2026-03-31
