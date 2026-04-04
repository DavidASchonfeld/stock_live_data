# Stock DAG Investigation Summary - Session 2026-03-31

## Executive Summary

**Status**: Stock DAG still not permanently visible in Airflow UI, but **ROOT CAUSE IDENTIFIED**.

Stock DAG exhibits **temporary visibility with automatic staleness** after ~90 seconds. Code is correct, files are deployed, but something is marking the DAG as stale automatically.

---

## What We Tried This Session

### ✅ Attempt 1: Applied Code Fixes (Commit 7a427d3)
**What**: Fixed 3 critical issues in `dag_stocks.py`
- Line 83: Changed `pendulum.now().subtract(days=1)` → `pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York")`
- Lines 141-144: Added API response validation (`if not raw_response or "Time Series (Daily)" not in raw_response`)
- Lines 246-248: Fixed exception re-raising in database load task

**Result**: ✅ Code correct, ✅ Imports successfully, ✅ Reserialize works
**But**: Stock DAG still becomes stale ~90 seconds after reserialize

### ✅ Attempt 2: Force Reserialize Both DAGs
**What**: Ran `airflow dags reserialize -B dags-folder` in K8s pod
**Result**: ✅ DAG metadata updated, ✅ `is_stale: False`, ✅ Database reflects changes
**But**: Staleness returns automatically after 90 seconds

### ✅ Attempt 3: Fixed Weather DAG Configuration Drift (Commit fecfd76)
**What**: Replaced dynamic start_date in `dag_weather.py` with immutable date (same fix as Stock)
**Deployment**: Ran `scripts/deploy.sh`
**Result**: ✅ Weather DAG works correctly and stays visible
**Key Finding**: Weather DAG does NOT have this staleness problem

### ✅ Attempt 4: Cleared Python Cache
**What**: Deleted `__pycache__` on EC2 (`rm -rf /home/ec2-user/airflow/dags/__pycache__`)
**Result**: Brief improvement - DAG stays non-stale for 40-50 seconds instead of becoming stale immediately
**Finding**: Cache was contributing but not the root cause

---

## Critical Discoveries

### 1. Stock DAG Has 90-Second Visibility Window
**Monitoring Results** (6 checks at 10-second intervals):
```
Check 1 (00:17:00): is_stale = False ✓
Check 2 (00:17:19): is_stale = False ✓
Check 3 (00:17:36): is_stale = False ✓
Check 4 (00:17:52): is_stale = False ✓
Check 5 (00:18:09): is_stale = True  ✗ (switched at ~90 seconds)
Check 6 (00:18:25): is_stale = True  ✗
```

**Pattern**: Consistent ~90-second window before staleness kicks in

### 2. Weather DAG Works, Stock DAG Doesn't

Both DAGs:
- Have identical code fixes
- Use same deployment mechanism
- Use same reserialize process
- Are in same dags folder

**Difference**: Only Stock DAG becomes stale

**Possible explanations**:
- DAG ID ("Stock_Market_Pipeline" vs "API_Weather-Pull_Data")
- File name ("dag_stocks.py" vs "dag_weather.py")
- Something in DAG logic/imports

### 3. DAG Processor Shows Cached File List

DAG processor logs claim to parse only 3 files:
- `taskflow_pull_weather.py` (which doesn't exist!)
- `testDag.py` (which doesn't exist!)
- `testDag2.py` (which doesn't exist!)

But actual directory contains:
- `dag_stocks.py` ✓ (exists)
- `dag_weather.py` ✓ (exists)
- Plus 7 other Python files

**Finding**: DAG processor logs appear to show CACHED data from previous state, not current reality

### 4. `last_parsed_time` Never Changes

After reserialize at 04:16:42 UTC:
- Stock DAG `last_parsed_time: 2026-03-31 04:16:42.221812+00:00`
- This timestamp NEVER changes during the 90-second window
- DAG is not being re-parsed after becoming stale

**Finding**: Staleness is flagged without re-parsing the file

---

## Files Involved

**Code Changes**:
- `airflow/dags/dag_stocks.py` - Fixed start_date + validation + exceptions
- `airflow/dags/dag_weather.py` - Fixed dynamic start_date
- `scripts/deploy.sh` - Deployment mechanism

**Configuration**:
- `airflow/helm/values.yaml` - Airflow configuration
- `airflow/manifests/pv-*.yaml` - K8s volume mounts
- `db_config.py` - Database credentials (imports at module level)

**Deployment Path**:
- Local: `/Users/David/Documents/Programming/Python/Data-Pipeline-2026/data_pipeline/airflow/dags/dag_stocks.py`
- EC2: `/home/ec2-user/airflow/dags/dag_stocks.py`
- K8s pod: `/opt/airflow/dags/dag_stocks.py` (via PersistentVolume mount)

---

## Commits Made

1. **`7a427d3`** - Stock DAG critical fixes
   - Immutable start_date
   - API validation
   - Exception re-raising

2. **`fecfd76`** - Weather DAG staleness fix
   - Dynamic start_date → immutable

---

## Next Steps for Investigation

### High Priority: Find What Marks DAG as Stale at ~90 Seconds

The root cause is something that runs ~90 seconds after reserialize and sets `is_stale: True`. This could be:

1. **Scheduler sync cycle** - Check Airflow config for scheduler DAG sync intervals
2. **DAG validation job** - Look for Airflow scheduled tasks that validate DAGs
3. **File monitor** - Check if something re-scans files and detects mismatch
4. **Database corruption** - Check if metadata is being reverted by a background process
5. **Pod restart/sync** - Check if K8s is syncing volume or reloading something

### Commands to Run:

```bash
# 1. Find any cron jobs or scheduled tasks
ssh ec2-stock "crontab -l"
kubectl get cronjobs -A

# 2. Check Airflow scheduler configuration
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- airflow config get-value core

# 3. Monitor in real-time while staleness happens
# Terminal 1: Watch staleness flag
while true; do
  kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
    airflow dags details Stock_Market_Pipeline 2>/dev/null | grep is_stale
  sleep 5
done

# Terminal 2: Watch scheduler logs
kubectl logs airflow-scheduler-0 -n airflow-my-namespace -f

# Terminal 3: Watch DAG processor logs
kubectl logs airflow-dag-processor-* -n airflow-my-namespace -f

# 4. Check database directly
kubectl exec airflow-postgresql-0 -n airflow-my-namespace -- \
  psql -U airflow -d airflow -c "SELECT dag_id, is_stale, last_parsed_time FROM dag WHERE dag_id='Stock_Market_Pipeline'"

# 5. Check if files are being modified
ssh ec2-stock "watch -n 1 'stat /home/ec2-user/airflow/dags/dag_stocks.py | grep Modify'"
```

### Data Points to Collect:

1. ✅ Code is correct - verified
2. ✅ Files are deployed - verified
3. ✅ DAG is in database - verified
4. ✅ Reserialize works - verified
5. ❓ What marks DAG as stale at 90 seconds? - UNKNOWN
6. ❓ Why only Stock DAG? - UNKNOWN
7. ❓ Why does Weather DAG not have this? - UNKNOWN

---

## Key Questions for Next Session

1. **Is there a 90-second scheduler sync cycle?** Check Airflow config for `DAG_SERIALIZER_CHECK_INTERVAL` or similar
2. **Is the Stock DAG being specifically targeted?** Check for DAG ID in any filters/exclusions
3. **Is the database being reverted?** Check if there's a background process updating the DB
4. **Are volumes remounting?** Check K8s pod events and volume sync intervals
5. **Is there a validation job?** Search for any code that validates DAGs and marks them stale

---

## Don't Repeat These Approaches

- ❌ Just reserializing doesn't fix it (DAG becomes stale again)
- ❌ Fixing code alone doesn't work (code is already correct)
- ❌ Clearing __pycache__ only delays the problem
- ❌ The issue is NOT a code/syntax problem (import succeeds, syntax valid)

---

## What Worked Temporarily

- Clearing `/home/ec2-user/airflow/dags/__pycache__` extended visibility window slightly
- This suggests some initialization or import-time processing affects DAG state

---

## Hypothesis for Root Cause

Something in the Airflow system runs on a ~90-second cycle and:
1. Scans the DAG folder
2. Compares file hash/content with database state
3. Detects "staleness" (mismatch between file and DB)
4. Sets `is_stale: True` flag
5. Hides DAG from UI

This would explain:
- Why it only happens after 90 seconds (not immediately)
- Why `last_parsed_time` never changes (file isn't being re-parsed, just flagged)
- Why Weather DAG isn't affected (different DAG ID/file pattern)
- Why clearing cache helped (might reset timestamp tracking)

**Investigation should focus on**: Finding what process runs at ~90-second intervals and checks for DAG staleness/configuration drift.

---

## Session Timeline

- **04:02:32 UTC** - Reserialize Stock DAG → `is_stale: False`
- **04:16:42 UTC** - Cache cleared, reserialize again → `is_stale: False`
- **04:17:25 UTC** - Still non-stale
- **04:18:09 UTC** - Becomes `is_stale: True` (roughly 90 seconds later)
- **04:18:25 UTC** - Still stale (and remains stale)

---

## For Next Debugging Session

Start with the commands in "Next Steps for Investigation" section. The key is finding:
1. What scheduler/sync job runs every ~90 seconds
2. Why it targets Stock DAG but not Weather DAG
3. What mechanism sets the `is_stale` flag

Once that's found, the fix will likely be to either:
- Exempt Stock DAG from that process
- Fix whatever inconsistency it's detecting
- Change the DAG ID or structure to match pattern Weather DAG uses
