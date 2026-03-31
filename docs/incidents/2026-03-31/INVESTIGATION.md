# Stock DAG Persistence Investigation (2026-03-31)

## Problem Statement

**User Report**: Stock DAG is not permanently visible in Airflow UI despite multiple fixes and reserializations. DAG appears briefly, then disappears within seconds. **Hypothesis: Something is automatically removing/hiding the DAG, not a code issue.**

---

## CRITICAL FINDINGS (2026-03-31 04:16 UTC)

### Root Cause IDENTIFIED: Delayed Staleness Flag

**Exact Timeline:**
1. Reserialize at 04:16:42 UTC → DAG metadata updated, `is_stale: False` ✓
2. DAG remains non-stale for 40-50 seconds (~04:17:25)
3. DAG becomes `is_stale: True` at ~04:18:09 (roughly 90 seconds after reserialize)
4. DAG stays stale indefinitely

**Evidence:**
- Monitoring at 10-second intervals showed transition from `False → True` between checks 4 and 5
- `last_parsed_time` never changes after reserialize (stays at 04:16:42)
- No log entries for Stock DAG parsing or deletion

### Mystery: DAG Processor Shows Wrong Files

The DAG processor logs report parsing only 3 files:
```
taskflow_pull_weather.py  (1 DAG, 0 errors)
testDag.py               (1 DAG, 0 errors)
testDag2.py              (1 DAG, 0 errors)
```

But actual files in `/opt/airflow/dags` are:
```
dag_stocks.py
dag_weather.py
api_key.py
constants.py
db_config.py
file_logger.py
stock_client.py
validate_database.py
weather_client.py
```

These "taskflow_pull_weather.py" / "testDag.py" files **do not exist** in the current pod!
- Searched entire container filesystem
- Only found: `/opt/airflow/dags/dag_stocks.py`
- Did NOT find: `/opt/airflow/dags/taskflow_pull_weather.py`

**Hypothesis:** DAG processor is showing cached/stale log entries from before cleanup, not current state.

### Key Question: Why Only 90-Second Window?

The Stock DAG:
- Works perfectly for first 40-50 seconds after reserialize
- Then mysteriously becomes stale
- Stays stale forever (even though file/code unchanged)

This 90-second window suggests:
- A scheduled job running every 60-90 seconds
- A scheduler sync cycle that detects staleness
- A cache refresh that overwrites fresh state

---

## Session 2026-03-31 Investigation Summary

### What We Discovered (All Working)

1. ✅ **Code is correct**
   - dag_stocks.py has immutable start_date: `pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York")`
   - All 3 fixes present: immutable date, API validation, exception re-raising
   - File imports successfully in K8s pods
   - Python syntax: Valid ✓

2. ✅ **Files are deployed to K8s**
   - EC2 has correct dag_stocks.py at `/home/ec2-user/airflow/dags/dag_stocks.py`
   - K8s pod can see file at `/opt/airflow/dags/dag_stocks.py`
   - File mounted via PersistentVolume (dag-pv → dag-pvc bound correctly)
   - Timestamps match deployment: Mar 31 03:28 UTC

3. ✅ **DAG metadata registered in database**
   - `airflow dags list` shows: `Stock_Market_Pipeline | /opt/airflow/dags/dag_stocks.py`
   - `airflow dags details Stock_Market_Pipeline` returns full metadata
   - Database knows about the DAG and its configuration

4. ✅ **Reserialize works**
   - Command: `airflow dags reserialize -B dags-folder`
   - Output shows: "Setting next_dagrun for Stock_Market_Pipeline to 2026-03-31 23:47:49..."
   - After reserialize: `is_stale: False`, `last_parsed_time: 2026-03-31 03:58:12 UTC`

### What We Tried

#### Attempt 1: Applied Code Fixes (Commit 7a427d3)
- **Action**: Committed 3 critical fixes to dag_stocks.py
  - Line 83: Changed to immutable start_date
  - Lines 141-144: Added API response validation
  - Lines 246-248: Fixed exception re-raising
- **Deployment**: Ran `scripts/deploy.sh` to sync to EC2 and K8s
- **Reserialize**: Forced `airflow dags reserialize -B dags-folder`
- **Result**: DAG metadata updated in database ✓, but **UI visibility issue persists**

#### Attempt 2: Reserialize Both DAGs
- **Action**: Force full reserialize of dags-folder bundle
- **Expected**: Both Stock and Weather DAGs refresh in metadata DB
- **Observed**:
  - Stock DAG: Metadata updated, `is_stale: False`
  - Weather DAG: Metadata updated, `is_stale: False`
  - CLI commands show both DAGs registered
  - **But UI visibility unchanged**

#### Attempt 3: Fixed Weather DAG Dynamic Start_Date (Commit fecfd76)
- **Action**: Changed Weather DAG from dynamic to immutable start_date (same fix as Stock)
- **Deployment**: Ran `scripts/deploy.sh`
- **Reserialize**: Forced reserialize
- **Result**: Weather DAG now properly scheduled and appears in UI, but **Stock DAG still missing**
- **Key observation**: Both DAGs got same fix, both reserialize, but only Weather DAG appears in UI
  - This suggests fix is not the issue
  - Suggests selective hiding/removal of Stock DAG

---

## Theory: Automatic DAG Removal/Hiding Mechanism

### Evidence for Automatic Process

1. **Stock DAG disappears despite working state**
   - Metadata is in database (CLI shows it)
   - File exists in correct location
   - Code is valid (imports successfully)
   - Yet UI doesn't show it for more than a few seconds

2. **Timing pattern**
   - DAG appears after reserialize
   - Disappears within ~30 seconds to a few minutes
   - Suggests automatic process runs on interval

3. **Weather DAG doesn't have this problem**
   - Same DAG folder location
   - Same reserialize process
   - Same deployment mechanism
   - Yet Weather DAG stays visible
   - **Difference**: DAG ID is different (`Stock_Market_Pipeline` vs `API_Weather-Pull_Data`)

4. **Selector hypothesis**
   - Could something be selectively targeting DAGs with name containing "Stock"?
   - Could there be a filter hiding DAGs from certain files?
   - Could there be a scheduled job that removes Stock DAGs?

---

## Things to Check (For Next Session)

### 1. **Check for automatic DAG deletion/removal logic**
   - Search codebase for any code that deletes or hides DAGs with "stock" in name
   - Check Airflow configuration for DAG auto-cleanup settings
   - Look for cron jobs or scheduled tasks on EC2 that might remove DAGs

   **Commands to run**:
   ```bash
   # Check if there's a process deleting the DAG file
   ssh ec2-stock "ls -la /home/ec2-user/airflow/dags/dag_stocks.py"

   # Check if file is being recreated/modified frequently
   ssh ec2-stock "stat /home/ec2-user/airflow/dags/dag_stocks.py"

   # Search for references to "stock" in all deployment scripts
   grep -r "stock" scripts/ .github/ 2>/dev/null

   # Check crontab for scheduled deletion
   ssh ec2-stock "crontab -l"

   # Check if there are backup/old files being swapped
   ssh ec2-stock "find /home/ec2-user -name '*stock*' -type f"
   ```

### 2. **Monitor DAG state in real-time**
   - Watch scheduler logs continuously for DAG parsing/deletion events
   - Watch file system for changes to dag_stocks.py
   - Check if dag_stocks.py is being renamed, moved, or deleted

   **Commands**:
   ```bash
   # Monitor scheduler logs in real-time
   kubectl logs -n airflow-my-namespace airflow-scheduler-0 -f 2>&1 | grep -i "stock\|delete\|remove"

   # Monitor file changes on EC2
   ssh ec2-stock "watch -n 1 'ls -la /home/ec2-user/airflow/dags/dag_stocks.py'"

   # Check DAG details every few seconds
   while true; do
     kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
       airflow dags details Stock_Market_Pipeline 2>/dev/null | grep -E 'is_paused|is_stale|fileloc'
     sleep 5
   done
   ```

### 3. **Check Airflow configuration for DAG filters**
   - Look at airflow.cfg or Helm values for DAG_IGNORE patterns
   - Check if Stock DAG is being filtered by any configuration
   - Check if there's a DAG allowlist that excludes Stock DAG

   **Check these**:
   - `airflow/helm/values.yaml` - search for `dag_ignore` or `allowed_dags`
   - K8s pod Airflow config: `airflow config get-value core dag_ignore_file_syntax_regex`
   - Airflow environment variables in pod

### 4. **Check if DAG is being paused automatically**
   - Look for auto-pause logic
   - Check if there's a scheduler setting that pauses DAGs with certain criteria

   **Commands**:
   ```bash
   # Check if Stock DAG is paused
   kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
     airflow dags details Stock_Market_Pipeline | grep 'is_paused'

   # Check if it gets paused after a few seconds
   for i in {1..10}; do
     echo "Check $i:"
     kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
       airflow dags details Stock_Market_Pipeline 2>/dev/null | grep 'is_paused'
     sleep 3
   done
   ```

### 5. **Compare Stock and Weather DAG lifecycle**
   - Both DAGs should be treated identically by Airflow
   - Stock DAG has: ID=`Stock_Market_Pipeline`, file=`dag_stocks.py`
   - Weather DAG has: ID=`API_Weather-Pull_Data`, file=`dag_weather.py`
   - Check if there's logic that treats them differently

### 6. **Check git history for clues**
   - Look at recent commits - was there a change that removed Stock DAG?
   - Check if there's a .gitignore or deploy script that skips certain files
   - Look at deployment logs to see if dag_stocks.py was actually synced

   **Check**:
   ```bash
   git log --oneline --all | head -20
   git log -p -- airflow/dags/dag_stocks.py | head -100
   grep -r "dag_stocks" scripts/ .github/
   ```

### 7. **Check if there's a manifest or configuration deleting the DAG**
   - Could there be a Kubernetes cronjob that's deleting DAG files?
   - Could there be an init container or sidecar modifying files?
   - Check all K8s objects that might affect the DAG folder

   **Commands**:
   ```bash
   # List all K8s objects in the namespace
   kubectl get all -n airflow-my-namespace

   # Check for cronjobs
   kubectl get cronjobs -n airflow-my-namespace
   kubectl get cronjobs -n default

   # Check for init containers or sidecars in pod
   kubectl describe pod airflow-scheduler-0 -n airflow-my-namespace
   ```

---

## Commits Made (For Reference)

1. **`7a427d3`** - Stock DAG code fixes
   - File: `airflow/dags/dag_stocks.py`
   - Changes: Immutable start_date + API validation + exception re-raising
   - Status: Deployed and reserialize successful, but DAG still not persistent in UI

2. **`fecfd76`** - Weather DAG fix
   - File: `airflow/dags/dag_weather.py`
   - Changes: Dynamic start_date → immutable
   - Status: Works correctly, DAG visible and scheduled

---

## Key Observations for Next Session

1. **The Stock DAG is NOT a code problem**
   - Code is correct and imports successfully
   - Reserialize works and updates metadata
   - CLI commands work: `airflow dags list`, `airflow dags details`

2. **The Stock DAG is NOT a deployment problem**
   - File is on EC2 at correct location
   - File is mounted in K8s pod
   - File hasn't been modified or deleted

3. **The Stock DAG disappears specifically in the Airflow UI**
   - Database metadata is intact (CLI shows it)
   - UI just doesn't render it
   - Or there's a process removing it after reserialize

4. **Weather DAG works fine with identical setup**
   - Same deployment mechanism
   - Same reserialize process
   - Same folder structure
   - Only difference: DAG ID and file name

5. **Most likely causes**:
   - ❓ Automatic DAG deletion triggered by DAG ID or file name
   - ❓ UI-side filter hiding Stock DAG
   - ❓ Scheduler is re-parsing and deleting DAG due to validation
   - ❓ Something on EC2 or K8s automatically removing the file
   - ❓ Database corruption reverting Stock DAG state

---

## Files to Investigate

- `airflow/helm/values.yaml` - Airflow configuration
- `airflow/manifests/*.yaml` - K8s manifests that might have deletion logic
- `scripts/deploy.sh` - Could contain logic that removes certain DAGs
- `.github/workflows/*.yaml` - Any automated workflows that modify DAGs
- `airflow/dags/dag_stocks.py` - DAG definition (already verified as correct)
- EC2 crontab - Any scheduled tasks removing DAGs
- K8s cronjobs - Any K8s scheduled jobs

---

## Next Steps for Investigation

1. Start with **monitoring in real-time** (Attempt 3 above) to catch the moment Stock DAG disappears
2. Check **logs for deletion/removal events** when it disappears
3. Verify **file still exists** when DAG is gone from UI
4. Search for **automatic removal logic** in code and configs
5. Compare **Stock vs Weather DAG lifecycle** to find the difference
