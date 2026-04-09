# T3 OpenLineage — Verification Checklist

## 1. Deploy
```bash
./scripts/deploy.sh
```
Rebuilds `airflow-dbt:3.1.8-dbt` with `openlineage-dbt` baked in and restarts Airflow pods.

---

## 2. Confirm package installed
```bash
kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- \
  /opt/dbt-venv/bin/pip show openlineage-dbt
```
**Pass:** prints `Name: openlineage-dbt` with a version number.

---

## 3. Trigger DAG and check task logs
1. Airflow UI → `dag_stocks` → trigger manually
2. `dbt_run` task → **Logs**
3. Look for OpenLineage JSON events, e.g.:
   ```json
   {"eventType": "START", "job": {"namespace": "pipeline", ...}, "inputs": [...], "outputs": [...]}
   ```
   One START + COMPLETE pair per dbt model.

**Pass:** JSON events appear. **Fail:** no events → check `OPENLINEAGE_CONFIG` prefix in BashOperator command.

---

## 4. No errors in scheduler logs
```bash
kubectl logs airflow-scheduler-0 -n airflow-my-namespace | grep -i openlineage | tail -20
```
**Pass:** no `ERROR` lines mentioning openlineage.

---

## 5. Dashboard still loads
Open `http://localhost:32147/dashboard/` — charts should render as before. T3 doesn't touch Snowflake or the dashboard; this is just a sanity check.
