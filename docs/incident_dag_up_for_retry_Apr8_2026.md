# Incident: DAGs Stuck in "Up for Retry" Status — Apr 8, 2026

## Summary
Both DAGs (`API_Weather-Pull_Data` and `Stock_Market_Pipeline`) were stuck in "Up for Retry" status, preventing execution and making logs inaccessible.

## Root Causes & Fixes

### Issue 1: Missing `alerting.py` Module (Parse Error)
**Symptom:** DAGs couldn't parse at all; "Up for Retry" appeared immediately after deployment.

**Root Cause:** Both DAGs imported from a non-existent module:
```python
from alerting import on_failure_alert, on_retry_alert, on_success_alert
```

**Fix:**
1. Created stub module `/airflow/dags/alerting.py` with no-op callback functions
2. Updated `scripts/deploy.sh` to validate all three DAG files during pre-flight checks (was only checking 2/3)
3. Re-deployed via `./scripts/deploy.sh`

**Result:** DAGs parsed successfully; Airflow UI showed them as healthy.

---

### Issue 2: Airflow 3.x Auth Token Signature Failure (Runtime Error)
**Symptom:** After manually triggering DAGs, they were marked "Up for Retry" despite tasks completing successfully in logs.

**Root Cause:** Scheduler couldn't report task status back to Airflow metadata DB:
```
ServerResponseError: Invalid auth token: Signature verification failed
```

This occurred in the scheduler→API communication layer (internal to Airflow, not DAG code).

**Fix:**
1. Restarted scheduler and API server pods to reset JWT/Fernet tokens:
   ```bash
   kubectl delete pod airflow-scheduler-0 airflow-api-server-* -n airflow-my-namespace
   ```
2. Waited for pods to reinitialize (2–3 minutes)
3. Verified with `airflow dags test` — both DAGs completed with `state=success`

**Result:** Tasks now report status correctly; "Up for Retry" cleared.

---

## Prevention

1. **Missing modules:** Always create stub implementations for callbacks/integrations (even no-ops) to prevent parse-time failures
2. **Auth failures:** Scheduler pod restarts resolve Airflow 3.x internal token issues; keep auth token expiry in mind if deploying frequently
3. **Testing:** Use `airflow dags test <dag_id>` to verify DAG execution end-to-end, not just parsing

## Lessons Learned

- Airflow 3.x SDK changed the `Variable.get()` API (already fixed in previous incident on Apr 7)
- `Variable.get()` now uses try/except instead of `default` parameter
- Scheduler pod restarts are safe and often resolve internal API auth issues
- The alerting callback infrastructure is now in place for future Slack/PagerDuty integration
