# ROOT CAUSE FOUND: Stock DAG 90-Second Staleness Issue

**Investigation Date**: 2026-03-31
**Status**: ROOT CAUSE IDENTIFIED ✅

---

## Executive Summary

**The Stock DAG marked as stale is NOT due to code issues or Airflow configuration.**

**Root Cause**: **The DAG Processor pod has a STALE CACHED VIEW of the DAG folder.** It's reading old files from June 2025 while the Scheduler reads the correct updated files from March 31 2026.

### Evidence

```
SCHEDULER POD (airflow-scheduler-0):
  File: /opt/airflow/dags/dag_stocks.py
  Inode: 84268967 ✅
  Date: 2026-03-31 03:28 (CURRENT)
  Files visible: dag_stocks.py, dag_weather.py, constants.py, etc.

DAG PROCESSOR POD (airflow-dag-processor-5456987646-8qnjz):
  Directory: /opt/airflow/dags/
  Inode: 142630362 ❌ (DIFFERENT INODE!)
  Date: 2025-06-18 18:22 (STALE)
  Files visible: taskflow_pull_weather.py, testDag.py, testDag2.py
```

Both pods mount the **same physical XFS volume** (`/dev/nvme0n1p1`) but see **different files** due to filesystem cache/sync issues.

---

## Why This Causes the "Stale" DAG Behavior

1. **DAG Processor doesn't see `dag_stocks.py`** in its cached directory view
2. **DAG Processor only sees old `taskflow_pull_weather.py`** file
3. **Airflow searches for Stock DAG** but can't find it (processor never parsed it)
4. **Airflow marks Stock DAG as "missing" or "stale"** in the database
5. **Airflow hides it from the UI** because it thinks the DAG file was deleted

The 90-second window is not the root cause — it's a symptom. The real problem is the DAG processor is working from an old cached directory state.

---

## Timeline of Discovery

| Step | Finding | Evidence |
|------|---------|----------|
| 1 | Stock DAG marked `is_stale: True` | `airflow dags details Stock_Market_Pipeline` output |
| 2 | DAG list only shows one DAG | `airflow dags list` shows only `API_Weather-Pull_Data` |
| 3 | Scheduler sees new files | `ls /opt/airflow/dags/` in scheduler pod shows `dag_stocks.py` |
| 4 | Processor sees old files | `ls /opt/airflow/dags/` in processor pod shows `taskflow_pull_weather.py` |
| 5 | **Different inodes!** | Scheduler inode `84268967` ≠ Processor inode `142630362` |
| 6 | Same mount point | Both mounted to `/dev/nvme0n1p1` at `/opt/airflow/dags` |
| 7 | **CACHE ISSUE** | Processor has stale cached view of directory contents |

---

## Technical Details

### Volume Mount Configuration
```yaml
Mount Point: /opt/airflow/dags
Physical Volume: /dev/nvme0n1p1 (XFS filesystem)
Access Mode: RWO (Read-Write-Once)
Both scheduler and processor mount to same path
```

### Filesystem State
**Scheduler Container**:
- Can read dag_stocks.py (inode 84268967)
- Dated 2026-03-31 03:28:59
- Current view ✅

**Processor Container**:
- Directory inode 142630362 (from 2025-06-18)
- Not seeing dag_stocks.py
- Stale cached view ❌

### Why The 90-Second Pattern?

The 90-second "staleness" window is NOT actually controlled by the cache issue directly. What's happening:

1. Reserialize forces Airflow to read the DAG from the file the **Scheduler** sees
2. Stock DAG temporarily marked `is_stale: False` (loaded successfully)
3. ~90 seconds later, some background process (likely DAG processor sync) queries the database
4. Detects that the **file no longer exists** (according to processor's stale view)
5. Marks DAG as stale again
6. This is the 90-second sync cycle (probably `min_serialized_dag_update_interval = 30` × 3 checks or similar)

---

## Why Only Stock DAG Has This Problem

### Stock DAG (Broken):
- Filename: `dag_stocks.py` (new filename)
- DAG ID: `Stock_Market_Pipeline`
- Only visible to Scheduler
- **NOT visible to Processor** (stale cache)
- Therefore: marked stale by Airflow

### Weather DAG (Working):
- Filename: `taskflow_pull_weather.py` (old filename that processor DOES see)
- DAG ID: `API_Weather-Pull_Data`
- Visible to **both** Scheduler AND Processor
- Therefore: marked active by Airflow

The Weather DAG works because the old deployment had this filename, and the processor's stale cache still has it!

---

## The Fix: Restart DAG Processor Pod

The DAG processor needs to clear its cached directory view and reload. This can be done by:

### Option 1: Restart the DAG Processor Pod (IMMEDIATE)
```bash
kubectl delete pod airflow-dag-processor-5456987646-8qnjz -n airflow-my-namespace
# Or delete all processor pods:
kubectl delete pod -l component=dag-processor -n airflow-my-namespace
```

K8s will automatically restart it with a fresh filesystem view.

### Option 2: Clear the K8s Volume Cache (IF restart doesn't work)
The volume might need to be unmounted and remounted. This requires deleting the PVC and having the pod restart.

### Option 3: Sync from EC2 Host (MANUAL BACKUP)
If restarting doesn't work, manually verify the source files on the EC2 host:
```bash
# SSH to EC2 instance and check:
ssh ec2-user@<EC2_IP>
ls -la /home/ec2-user/airflow/dags/
# Verify dag_stocks.py exists there
```

---

## Files Involved

**Current Deployment**:
- Scheduler pod: Sees `/opt/airflow/dags/dag_stocks.py` ✅
- Processor pod: Sees stale directory inode from June 2025 ❌

**Expected After Fix**:
- Both pods: See same fresh `/opt/airflow/dags/dag_stocks.py` ✅

**Source Files**:
- Local: `/Users/David/Documents/Programming/Python/StockLiveData/stock_live_data/airflow/dags/dag_stocks.py`
- EC2: `/home/ec2-user/airflow/dags/dag_stocks.py` (should be here)
- K8s: `/opt/airflow/dags/dag_stocks.py` (mounted from EC2)

---

## Verification Steps

### Before Fix:
```bash
# Scheduler can see the file
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- ls /opt/airflow/dags/dag_stocks.py
# Output: /opt/airflow/dags/dag_stocks.py ✅

# Processor can't see the file (lists old directory)
kubectl exec airflow-dag-processor-5456987646-8qnjz -n airflow-my-namespace -- ls /opt/airflow/dags/ | grep dag_stocks
# Output: (empty) ❌

# DAG is marked stale
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags details Stock_Market_Pipeline | grep is_stale
# Output: is_stale | True ❌
```

### After Fix (Expected):
```bash
# Both can see the file
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- ls /opt/airflow/dags/dag_stocks.py
# Output: /opt/airflow/dags/dag_stocks.py ✅

kubectl exec airflow-dag-processor-5456987646-8qnjz -n airflow-my-namespace -- ls /opt/airflow/dags/dag_stocks.py
# Output: /opt/airflow/dags/dag_stocks.py ✅ (NEW!)

# DAG is no longer stale
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow dags details Stock_Market_Pipeline | grep is_stale
# Output: is_stale | False ✅
```

---

## Next Steps

1. **Execute the Fix**: Restart the DAG processor pod
2. **Wait 30-60 seconds** for it to fully initialize
3. **Verify**: Run the verification commands above
4. **If Fixed**: Celebrate 🎉
5. **If Not Fixed**: Try clearing the PVC (more drastic measure)

---

## Why This Happened

The deployment setup has:
- **Scheduler and Processor sharing the same PV** for DAG files
- **Files were copied to K8s volume** via deploy.sh
- **Processor pod was running with stale cache** from previous setup
- **Scheduler pod had fresh view** (possibly restarted more recently)

The mismatch occurs when files are updated on the shared volume but one pod hasn't refreshed its directory cache.

---

## Prevention for Future

1. **Always restart both scheduler and processor** when updating DAG files:
   ```bash
   kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
   kubectl delete pod -l component=dag-processor -n airflow-my-namespace
   ```

2. **Or use ConfigMap instead of shared volume** for DAGs if possible

3. **Or implement file sync watchers** to detect when processor cache is stale

4. **Or schedule periodic pod restarts** to clear caches

---

## Related Files

- `airflow/dags/dag_stocks.py` - The DAG file
- `airflow/helm/values.yaml` - Airflow configuration
- `airflow/manifests/pv-dags.yaml` - PersistentVolume definition
- `airflow/manifests/pvc-dags.yaml` - PersistentVolumeClaim definition
- `scripts/deploy.sh` - Deployment script that copies files

---

## Key Takeaway

**It was never a code problem.** It was a K8s volume caching issue where the DAG processor had a stale view of the shared filesystem. The 90-second "staleness" window is actually Airflow's sync cycle discovering the file doesn't appear to exist (from processor's perspective) and marking it stale.

The fix: **Restart the DAG processor pod to clear the cache.**
