# Failure Mode Map

A proactive catalog of how each component in this system can fail, why it fails, and what the symptoms look like. Organized by component, ranked by likelihood based on real incidents.

**Navigation:**
- Want to see how failures cascade between components? See [COMPONENT_INTERACTIONS.md](COMPONENT_INTERACTIONS.md)
- Need validation strategies at each pipeline stage? See [DATA_FLOW.md](DATA_FLOW.md)
- Looking for prevention patterns? See [../operations/PREVENTION_CHECKLIST.md](../operations/PREVENTION_CHECKLIST.md)

---

## How to Use This Document

When something breaks, find the component showing symptoms in the relevant section below. Each failure mode includes symptoms, root cause, blast radius, and whether it has occurred in this project.

---

## Sections by Component

| Component | Failure modes | Guide |
|-----------|--------------|-------|
| Airflow (Scheduler + DAG Processor) | AF-1 through AF-8 | [failure-modes/airflow.md](failure-modes/airflow.md) |
| Flask / Dash (API + Dashboard) | FL-1 through FL-5 | [failure-modes/flask-dash.md](failure-modes/flask-dash.md) |
| K3s / Kubernetes | K8-1 through K8-6 | [failure-modes/kubernetes.md](failure-modes/kubernetes.md) |
| AWS EC2 / Infrastructure | EC-1 through EC-5 | [failure-modes/ec2-infrastructure.md](failure-modes/ec2-infrastructure.md) |
| API Layer (SEC EDGAR / Open-Meteo) | API-1 through API-5 | [failure-modes/api-layer.md](failure-modes/api-layer.md) |

---

## Quick Lookup: "I See This Symptom, What Is It?"

| Symptom | Most likely failure mode |
|---------|------------------------|
| DAG appears then vanishes after ~30s | AF-1 (config drift) |
| DAG appears then vanishes after ~90s | AF-5 (processor cache) |
| DAG never appears, no errors | AF-2 (module variable) |
| All Airflow pods stuck Init:0/1 | AF-3 (PostgreSQL down or migration job blocked) |
| All pods CreateContainerConfigError after helm upgrade | AF-7 (missing chart secret — run with enableBuiltInSecretEnvVars fix) |
| Scheduler OOMKilled every few minutes after upgrade | AF-6 (2Gi memory limit needed for Airflow 3.x) |
| helm upgrade accidentally jumped major versions, can't roll back | AF-8 (DB schema upgraded — move forward, don't roll back) |
| Pod shows ImagePullBackOff | FL-2 (ECR token) or AF-3 (deleted image) |
| Port unreachable, pod is Running | K8-3 or FL-5 (selector mismatch) |
| Pod empty directory, files on EC2 | K8-1 (PV path mismatch) |
| Fix deployed but pod still crashing | K8-2 (backoff inertia) |
| All static assets fail simultaneously | K8-6 (webserver OOMKill) |
| values.yaml change has no effect | K8-6 / missing helm upgrade step |
| SSH timeout from new location | EC-1 (IP restriction) |
| API returns data but it's wrong | API-2 (schema change) |
| Dashboard shows old data, no errors | FL-4 (stale data, silent DAG failure) |

---

**Last updated:** 2026-04-06 — Added AF-6 (scheduler OOMKill in 3.x), AF-7 (missing chart secret after upgrade), AF-8 (helm upgrade without version pin); updated AF-3; updated quick lookup table.
