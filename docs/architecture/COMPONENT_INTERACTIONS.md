# Component Interactions & Cascade Failures

How components in this system depend on each other, where failures cascade between layers, and what the blast radius is when each component goes down.

**Navigation:**
- Want the full failure catalog? → [FAILURE_MODE_MAP.md](FAILURE_MODE_MAP.md)
- Need validation strategies? → [DATA_FLOW.md](DATA_FLOW.md)
- Looking for prevention patterns? → [../operations/PREVENTION_CHECKLIST.md](../operations/PREVENTION_CHECKLIST.md)

---

## Dependency Graph

```
                    ┌──────────────┐
                    │  AWS EC2     │
                    │  Instance    │
                    │  (t3.xlarge) │
                    └──────┬───────┘
                           │ hosts
                    ┌──────▼───────┐
                    │    K3s       │
                    │  (single     │
                    │   node)      │
                    └──┬───┬───┬───┘
              ┌────────┘   │   └────────┐
              │            │            │
    ┌─────────▼──┐  ┌──────▼─────┐  ┌──▼──────────┐
    │  Airflow    │  │  MariaDB   │  │  Flask/Dash  │
    │  Cluster    │  │  (DB)      │  │  (Dashboard) │
    │            │  │            │  │             │
    │ ┌────────┐ │  │  stores:   │  │  reads from │
    │ │Postgres│ │  │  - stocks  │  │  MariaDB    │
    │ │(meta)  │ │  │  - weather │  │             │
    │ └────────┘ │  │            │  │  serves to  │
    │            │  │            │  │  browser    │
    │ ┌────────┐ │  └──────▲─────┘  └──▲──────────┘
    │ │Schedul.│ │         │            │
    │ │Process.│──────writes to──────reads from
    │ │Trigger.│ │         │            │
    │ │API Srv.│ │         │            │
    │ └────────┘ │         │            │
    │            │         │            │
    │  calls ────┼─────────┼────────────┘
    └─────┬──────┘
          │ calls
    ┌─────▼────────┐
    │ External APIs │
    │ - SEC EDGAR │
    │ - Open-Meteo  │
    └──────────────┘
```

---

## Blast Radius Analysis

For each component: what happens when it goes down, who's affected, and what continues working.

### If EC2 Instance Goes Down

```
EC2 DOWN
  └─→ K3s cluster gone
       └─→ ALL pods gone
            ├─→ Airflow: no DAG runs, no data ingestion
            ├─→ MariaDB: no reads or writes (data preserved on EBS if not terminated)
            ├─→ Flask: dashboard unreachable
            └─→ SSH tunnel: broken, no access to anything
```

**Blast radius:** Total outage. Nothing works.
**Recovery:** Start EC2 instance → K3s auto-starts → pods auto-restart → verify all endpoints.
**Data loss:** None if using EBS-backed PVs (data survives instance stop). Full loss if instance terminated without EBS snapshots.

---

### If K3s Stops (but EC2 still running)

```
K3s DOWN (EC2 UP)
  └─→ All pods stopped
       ├─→ Airflow: no DAG runs
       ├─→ MariaDB pod: down (but data on host filesystem)
       └─→ Flask: dashboard down
  SSH still works → can diagnose and restart K3s
  MariaDB host-install (if exists) still accessible directly
```

**Blast radius:** All K8s workloads down. SSH access retained.
**Recovery:** `sudo systemctl restart k3s` on EC2.

---

### If Internal PostgreSQL Goes Down

```
PostgreSQL DOWN
  └─→ Airflow init containers block (wait-for-airflow-migrations)
       └─→ ALL Airflow pods stuck at Init:0/1
            ├─→ Scheduler: can't schedule DAGs
            ├─→ API Server: Airflow UI unreachable
            ├─→ Triggerer: can't trigger deferred tasks
            └─→ DAG Processor: can't parse DAGs
  MariaDB: UNAFFECTED (separate database, separate pod)
  Flask: UNAFFECTED (reads from MariaDB, not PostgreSQL)
  Dashboard: shows existing data but no new data arrives
```

**Blast radius:** Total Airflow outage. Dashboard serves stale data but stays up.
**This is the most deceptive failure** — Flask works fine, so you might not notice data stopped flowing until you check timestamps.
**Recovery:** Fix PostgreSQL pod → all Airflow pods auto-unblock.

---

### If MariaDB Goes Down

```
MariaDB DOWN
  ├─→ Airflow DAG load() tasks: FAIL (can't insert data)
  │    └─→ Extract and transform tasks still succeed (no DB dependency)
  │    └─→ Data computed but thrown away (load step fails)
  ├─→ Flask: 500 errors on all data endpoints
  │    └─→ Dashboard: shows errors instead of charts
  └─→ Airflow scheduling: UNAFFECTED (uses PostgreSQL, not MariaDB)
      └─→ DAGs still trigger on schedule, extract and transform run,
          but load fails every time
```

**Blast radius:** Data pipeline incomplete (extract+transform work, load fails). Dashboard down.
**Data loss:** Computed DataFrames are lost if not cached. Once MariaDB recovers, the next DAG run re-fetches and inserts.
**Recovery:** Fix MariaDB pod → trigger missed DAG runs to backfill.

---

### If Airflow Scheduler Goes Down

```
Scheduler DOWN
  └─→ No new DAG runs triggered
       └─→ No new data ingested
  API Server: may still serve Airflow UI (shows last known state)
  MariaDB: UNAFFECTED (just stops receiving new writes)
  Flask: serves existing data (increasingly stale)
  DAG Processor: continues parsing DAGs but can't hand off to scheduler
```

**Blast radius:** Data goes stale. Everything else stays up.
**Recovery:** StatefulSet auto-recreates the pod. If stuck, `kubectl delete pod airflow-scheduler-0`.

---

### If Flask Pod Goes Down

```
Flask DOWN
  └─→ Dashboard: unreachable (port 32147 returns nothing)
  Airflow: COMPLETELY UNAFFECTED
  MariaDB: COMPLETELY UNAFFECTED
  Data pipeline: continues running normally
```

**Blast radius:** Minimal. Only the dashboard is affected. Data continues flowing.
**Recovery:** Pod auto-restarts. If `ImagePullBackOff`, refresh ECR token first.

---

### If External APIs Are Down

```
APIs DOWN
  └─→ Airflow extract tasks: FAIL (timeout or connection error)
       └─→ Downstream transform + load: never execute
       └─→ DAG run marked failed
  MariaDB: UNAFFECTED (retains all existing data)
  Flask: serves existing data (stale but functional)
  Airflow itself: healthy, just can't fetch new data
```

**Blast radius:** No new data. Everything else works. Invisible to dashboard users unless they check data timestamps.
**Recovery:** Wait for API to recover. Trigger missed DAG runs to backfill.

---

## Cascade Failure Chains

These are the dangerous multi-step failures where one problem triggers another.

### Chain 1: Image Deletion → PostgreSQL Down → Total Airflow Outage

```
Docker Hub deletes Bitnami PostgreSQL image tag
  → airflow-postgresql-0 enters ImagePullBackOff
    → init containers block on all Airflow pods
      → Scheduler, API Server, Triggerer, Processor all stuck Init:0/1
        → Zero DAG runs, zero data ingestion, Airflow UI unreachable
```

**How to break the chain:** Pin image tags to SHA digests instead of mutable tags. Or mirror critical images to your own ECR registry.

**Real incident:** 2026-03-30. This exact chain happened.

### Chain 2: Deploy Path Change → PV Mismatch → Invisible DAGs

```
deploy.sh sync path changed (or PV manifest edited)
  → Files land at /home/ec2-user/airflow/dags/
  → PV still points to /tmp/airflow-dags/
    → Pod mounts empty directory
      → Airflow sees no DAGs
        → No DAG runs, no errors (completely silent)
```

**How to break the chain:** Validate PV path matches deploy path before every deploy.

**Real incident:** 2026-03-31. This exact chain happened.

### Chain 3: Secret Update → Forgot Restart → Stale Credentials → Silent Auth Failure

```
Database password rotated
  → K8s Secret updated in airflow-my-namespace
  → Forgot to update in default namespace
  → Forgot to restart pods
    → Airflow pods: work (have new secret from next restart)
    → Flask pod: fails with Access Denied (has old secret)
      → Dashboard down, pipeline continues
```

**How to break the chain:** Checklist: update secret in BOTH namespaces → restart pods in BOTH namespaces → verify env vars inside pods.

### Chain 4: Rate Limit → Unvalidated Response → Data Corruption

```
SEC EDGAR rate limit exceeded
  → API returns {"Note": "Thank you for using..."} with HTTP 200
  → Extract task doesn't validate response body
    → Returns rate-limit message as "data"
      → Transform task tries json_normalize on wrong structure
        → Either: loud failure (KeyError) — GOOD
        → Or: silent garbage DataFrame — BAD
          → Load inserts garbage into MariaDB
            → Flask serves corrupted data
```

**How to break the chain:** Validate API response structure before returning from extract. Check for known error patterns (`"Note"` key, `"Information"` key).

**Partially addressed:** Stock DAG now validates responses. Weather DAG should get same treatment.

### Chain 5: Disk Full → Cascading Write Failures

```
EBS volume fills up (logs + images + data)
  → MariaDB can't write: INSERT failures
  → Airflow can't write logs: scheduler crashes
  → containerd can't pull images: ImagePullBackOff on restarts
  → K3s can't write etcd data: cluster instability
    → Everything fails simultaneously with different error messages
      → Root cause (disk full) hidden behind diverse symptoms
```

**How to break the chain:** Monitor disk usage. Set up log rotation. Prune old container images periodically.

---

## Failure Independence Map

Which components can fail independently without affecting others:

| Component Down | Airflow | MariaDB | Flask | Dashboard | APIs |
|---------------|---------|---------|-------|-----------|------|
| **EC2** | DOWN | DOWN | DOWN | DOWN | unaffected (external) |
| **K3s** | DOWN | DOWN | DOWN | DOWN | unaffected |
| **PostgreSQL** | DOWN | ok | ok | stale data | ok |
| **MariaDB** | load fails | DOWN | 500 errors | DOWN | ok |
| **Scheduler** | no new runs | ok | ok | stale data | ok |
| **Flask** | ok | ok | DOWN | DOWN | ok |
| **APIs** | extract fails | ok | ok | stale data | DOWN |
| **Disk full** | DOWN | DOWN | DOWN | DOWN | ok |

Key insight: **MariaDB and PostgreSQL are the two most critical internal dependencies.** PostgreSQL down → total Airflow outage. MariaDB down → no data storage and no dashboard. Everything else has limited blast radius.

---

## Monitoring Priorities (What to Watch)

Based on blast radius analysis, monitor these in order of importance:

1. **EC2 instance state** — Total outage if down (AWS CloudWatch)
2. **PostgreSQL pod health** — Total Airflow outage if down (`kubectl get pods`)
3. **MariaDB connectivity** — Pipeline + dashboard break if down (TCP check on 3306)
4. **Disk usage** — Cascading failures when full (`df -h`)
5. **DAG run success/failure** — Data freshness depends on this (Airflow UI or API)
6. **Service endpoints** — Services silently break without them (`kubectl get endpoints`)
7. **Data freshness** — Latest row timestamp in MariaDB (custom health endpoint)

---

**Last updated:** 2026-03-31
