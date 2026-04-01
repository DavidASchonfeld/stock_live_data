# Prevention Checklist

Checklists to run before, during, and after common operations. The goal: catch problems before they reach production, not after.

**Navigation:**
- Understanding why these checks matter? → [../architecture/FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md)
- Step-by-step operational procedures? → [RUNBOOKS.md](RUNBOOKS.md)
- Debugging when prevention fails? → [TROUBLESHOOTING.md](TROUBLESHOOTING.md)

---

## Per-Deploy Checklist (every `deploy.sh` run)

Run through this mentally (or literally) every time you deploy code changes.

### Before Deploy

- [ ] **DAG syntax valid locally** — `python -c "import dag_stocks; import dag_weather"` passes
- [ ] **No dynamic start_dates** — Grep for `pendulum.now()` or `datetime.now()` in DAG-level config. Must be zero matches.
  ```bash
  grep -n "pendulum.now\|datetime.now" airflow/dags/dag_*.py
  ```
- [ ] **Module-level DAG variable exists** — Each DAG file ends with `dag = function_name()` assignment
- [ ] **Required secrets validated at import** — `_required_secrets` list present in each DAG
- [ ] **PV path matches deploy path** — Compare `EC2_DAG_PATH` in `deploy.sh` with `hostPath.path` in `airflow/manifests/pv-dags.yaml`. Must match.
  ```bash
  grep EC2_DAG_PATH scripts/deploy.sh
  grep "path:" airflow/manifests/pv-dags.yaml
  ```

### During Deploy

- [ ] **deploy.sh completes all steps** — Watch for errors in each numbered step. Don't ignore warnings.
- [ ] **rsync shows expected file count** — Verify the right number of files transferred

### After Deploy

- [ ] **Files exist inside the pod** (not just on EC2)
  ```bash
  ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- ls -la /opt/airflow/dags/
  ```
- [ ] **Restart both scheduler AND processor pods** (prevents 90s staleness — see [AF-5](../architecture/FAILURE_MODE_MAP.md#af-5-dag-processor-filesystem-cache-stale))
  ```bash
  ssh ec2-stock kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
  ssh ec2-stock kubectl delete pod -l component=dag-processor -n airflow-my-namespace
  ```
- [ ] **Wait 60 seconds, then verify DAGs visible**
  ```bash
  ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow dags list
  ```
- [ ] **Check `next_dagrun` is set** — Confirms scheduler registered the DAG for future runs
- [ ] **Monitor first DAG run to completion** — Don't walk away until the first post-deploy run succeeds

---

## Per-Infrastructure-Change Checklist

For changes to K8s manifests, Helm values, PV/PVC configs, or service definitions.

### Before Applying

- [ ] **Diff the change** — `git diff` the manifest. Understand exactly what changed.
- [ ] **PV paths consistent** — If touching PV manifests, verify `hostPath.path` matches `deploy.sh` sync target
- [ ] **Service selectors match pod labels** — If touching services, verify selector matches actual pod labels
  ```bash
  # What selector does the service use?
  ssh ec2-stock kubectl describe svc <service-name> -n <namespace> | grep Selector
  # What labels do pods have?
  ssh ec2-stock kubectl get pods -n <namespace> --show-labels
  ```
- [ ] **Helm values.yaml changes backported** — If you made manual `kubectl patch` fixes earlier, are they reflected in `values.yaml`? Next `helm upgrade` will overwrite manual changes.

### After Applying

- [ ] **All pods reach Running state**
  ```bash
  ssh ec2-stock kubectl get pods --all-namespaces
  ```
- [ ] **No pods stuck in CrashLoopBackOff** — If any are, force-delete them (see [K8-2](../architecture/FAILURE_MODE_MAP.md#k8-2-crashloopbackoff-inertia))
- [ ] **Services have endpoints** (not `<none>`)
  ```bash
  ssh ec2-stock kubectl get endpoints -n airflow-my-namespace
  ssh ec2-stock kubectl get endpoints -n default
  ```
- [ ] **PVs are Bound** (not Released or Available)
  ```bash
  ssh ec2-stock kubectl get pv,pvc -A
  ```
- [ ] **Secrets exist in both namespaces** (if credential-related change)
  ```bash
  ssh ec2-stock kubectl get secret db-credentials -n airflow-my-namespace
  ssh ec2-stock kubectl get secret db-credentials -n default
  ```
- [ ] **Pods restarted after secret update** — Secrets don't hot-reload into running pods

---

## Per-Helm-Upgrade Checklist

Helm upgrades are the highest-risk operation — they can change multiple resources simultaneously.

### Before Upgrade

- [ ] **Current state is healthy** — Don't upgrade into an already-broken cluster
- [ ] **Helm diff** (if helm-diff plugin installed) or review `values.yaml` changes carefully
- [ ] **No manual patches outstanding** — Any `kubectl patch` fixes must be in `values.yaml` first
- [ ] **Know the rollback plan** — `helm rollback airflow <previous-revision> -n airflow-my-namespace`
  ```bash
  # Check current revision:
  ssh ec2-stock helm history airflow -n airflow-my-namespace
  ```

### After Upgrade

- [ ] **PostgreSQL pod healthy FIRST** — Everything else depends on this
- [ ] **All init containers complete** — Pods should progress past `Init:0/1`
- [ ] **Force-delete any CrashLoopBackOff pods** — They may be stuck on old backoff timer
- [ ] **Run full health check** (see [Quick Health Check](TROUBLESHOOTING.md) section)
- [ ] **Verify Airflow UI accessible** — `http://localhost:30080` via SSH tunnel
- [ ] **Verify both DAGs visible and unpaused**

---

## Secret Rotation Checklist

When changing database passwords, API keys, or other credentials.

- [ ] **Update credential at the source** (e.g., MariaDB `ALTER USER`)
- [ ] **Recreate K8s Secret in `airflow-my-namespace`**
  ```bash
  kubectl create secret generic db-credentials \
    -n airflow-my-namespace \
    --from-literal=DB_USER=... --from-literal=DB_PASSWORD=... \
    --from-literal=DB_NAME=... --from-literal=DB_HOST=... \
    \
    --dry-run=client -o yaml | kubectl apply -f -
  ```
- [ ] **Recreate K8s Secret in `default` namespace** (same command, `-n default`)
- [ ] **Restart ALL pods that use the secret**
  ```bash
  # Airflow pods
  kubectl rollout restart statefulset airflow-scheduler -n airflow-my-namespace
  kubectl rollout restart deployment airflow-api-server -n airflow-my-namespace
  kubectl rollout restart statefulset airflow-triggerer -n airflow-my-namespace
  # Flask pod
  kubectl delete pod my-kuber-pod-flask -n default
  ```
- [ ] **Verify env vars inside pods**
  ```bash
  kubectl exec airflow-scheduler-0 -n airflow-my-namespace -- env | grep DB_
  kubectl exec my-kuber-pod-flask -n default -- env | grep DB_
  ```
- [ ] **Trigger a test DAG run** — Confirm end-to-end data flow works with new credentials
- [ ] **Update `infra_local.md`** — Keep local secret reference current (gitignored)

---

## New Location / IP Change Checklist

When working from a new location (home, office, travel).

- [ ] **Find your current public IP** — `curl ifconfig.me`
- [ ] **Update EC2 security group** — AWS Console → EC2 → Security Groups → edit inbound SSH rule
- [ ] **Test SSH** — `ssh ec2-stock` connects
- [ ] **Re-establish SSH tunnel** — `ssh -L 30080:localhost:30080 -L 32147:localhost:32147 ec2-stock`
- [ ] **Update `infra_local.md`** — Record new IP (gitignored)

---

## Weekly Health Check

Run this weekly to catch slow-burn issues before they become incidents.

- [ ] **Disk usage < 80%** — `ssh ec2-stock df -h`
- [ ] **All pods Running** — `ssh ec2-stock kubectl get pods --all-namespaces`
- [ ] **Services have endpoints** — `ssh ec2-stock kubectl get endpoints -A`
- [ ] **Data is fresh** — Check latest row timestamps in both tables
  ```bash
  ssh ec2-stock "kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- python3 -c \"
  from sqlalchemy import create_engine, text
  import os
  engine = create_engine(f'mysql+pymysql://{os.environ[\"DB_USER\"]}:{os.environ[\"DB_PASSWORD\"]}@{os.environ[\"DB_HOST\"]}/{os.environ[\"DB_NAME\"]}')
  with engine.connect() as c:
      r = c.execute(text('SELECT MAX(filed_date) FROM company_financials')).scalar()
      print(f'company_financials: latest filed = {r}')
      r = c.execute(text('SELECT MAX(imported_at) FROM weather_hourly')).scalar()
      print(f'weather_hourly: latest import = {r}')
  \""
  ```
- [ ] **DAGs unpaused and scheduled** — `airflow dags list` shows `paused=False`, `next_dagrun` set
- [ ] **No CrashLoopBackOff history** — `kubectl get events --all-namespaces --sort-by=.lastTimestamp | grep BackOff`
- [ ] **Prune old container images** (if disk usage climbing)
  ```bash
  ssh ec2-stock sudo crictl rmi --prune
  ```

---

## DAG Authoring Checklist

When creating or modifying a DAG file.

### Configuration

- [ ] `start_date` is a fixed past date (never `pendulum.now()` or `datetime.now()`)
- [ ] `@dag` return value assigned to module-level variable (`dag = my_pipeline()`)
- [ ] `schedule` parameter set (or explicitly `None` for manual-only)
- [ ] `catchup=False` set (unless you specifically want backfilling)
- [ ] `_required_secrets` list validates environment variables at import time

### Extract Task

- [ ] HTTP timeout set on `requests.get()` (e.g., `timeout=30`)
- [ ] Response status code checked
- [ ] Response body validated (not empty, not rate-limit, not HTML)
- [ ] Expected JSON keys verified before returning
- [ ] Exceptions re-raised after logging (never swallowed silently)

### Transform Task

- [ ] DataFrame has expected columns after `json_normalize()`
- [ ] DataFrame has > 0 rows
- [ ] Data types validated (numeric columns are numeric)
- [ ] Values in plausible ranges
- [ ] Output uses `to_dict(orient="records")` for clean XCom serialization

### Load Task

- [ ] DataFrame columns match DB table schema before `to_sql()`
- [ ] Duplicate check (don't insert rows already in DB)
- [ ] Post-insert row count verified
- [ ] Connection errors handled with clear error message

---

**Last updated:** 2026-03-31
