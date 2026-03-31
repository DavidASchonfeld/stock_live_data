# Documentation Index

Complete navigation guide for all project documentation. Start with your goal, then follow the links.

---

## 🚀 Getting Started

**New to this project?**

1. **README.md** (in root) — Quick overview, architecture summary, links to all guides
2. **OVERVIEW.md** (in root) — Comprehensive setup guide; read this before deploying
3. **ARCHITECTURE.md** — Understand why K3S, how Kubernetes works, how data flows

---

## 📚 Understanding the System

### Conceptual Guides
- **ARCHITECTURE.md** — Deep dive into:
  - Why K3S (cost efficiency, lightweight Kubernetes)
  - Docker vs containerd (container runtimes)
  - Kubernetes concepts (Pods, Services, PersistentVolumes)
  - Your ETL pipeline (extract → transform → load)
  - How Airflow, MariaDB, and Flask connect

### Glossary & Reference
- **GLOSSARY.md** — Define technical terms:
  - Abbreviations: SMA, ETL, DAG, PV, PVC, K3S, etc.
  - Tools: Kubernetes, Helm, containerd, Docker, MariaDB
  - Concepts: Namespaces, NodePorts, health probes, etc.

- **COMMANDS.md** — Understand cryptic shell commands:
  - `ss -tlnp` — Port/socket debugging
  - `rsync -avz` — File synchronization
  - `kubectl get pods -w` — Real-time pod monitoring
  - Docker build/push workflow

- **KUBECTL_COMMANDS.md** — Complete Kubernetes CLI reference

---

## 🔧 Operations & Troubleshooting

### Debugging
- **DEBUGGING.md** — Systematic debugging approach:
  - Mental model of the system
  - Common issue categories (A–I)
  - Diagnostic sequences
  - Pod crash troubleshooting
  - API error resolution

- **TROUBLESHOOTING.md** — Solutions to specific problems:
  - DAG discovery issues
  - Module-level variable problems
  - Airflow UI access issues

### Infrastructure & Deployment
- **ECR_SETUP.md** — AWS Elastic Container Registry:
  - One-time ECR configuration
  - Docker image push workflow
  - Authentication to ECR from EC2

- **COMMANDS.md** (referenced above) — Command reference for debugging and ops

---

## 📋 Incidents & History

- **FIXES_AIRFLOW_2026-03-30.md** — Airflow pod failures analysis:
  - Root cause: PostgreSQL image, values.yaml config, DB credentials
  - Resolution steps

- **STATUS_2026-03-30.md** — Operational snapshot as of 2026-03-30:
  - What was deployed
  - Known issues
  - Next steps

- **CHANGELOG.md** — All fixes and changes over time

---

## 🏗️ Architecture & Infrastructure Files

**In project root (Git-tracked):**
- `airflow/manifests/` — Kubernetes manifests for Airflow
  - `pv-dags.yaml` — PersistentVolume for DAG files
  - Helm values for Airflow deployment

- `dashboard/manifests/` — Flask + Dash pod configuration
  - `pod-flask.yaml` — Pod definition

- `airflow/dags/` — Your ETL workflows
  - `dag_stocks.py` — Stock data pipeline
  - `dag_weather.py` — Weather data pipeline

- `scripts/` — Extraction scripts
  - `stock_client.py` — Fetch from Alpha Vantage API
  - `weather_client.py` — Fetch from Open-Meteo API

**In project root (NOT Git-tracked):**
- `infra_local.md` — Local EC2 secrets and IP restrictions (gitignored)

---

## 🎯 Common Questions

### "What is K3S and why do we use it?"
→ [ARCHITECTURE.md](ARCHITECTURE.md#why-k3s)

### "What is a PersistentVolume?"
→ [GLOSSARY.md](GLOSSARY.md#persistentvolume-pv) or [ARCHITECTURE.md](ARCHITECTURE.md#persistentvolumes-pv-and-persistentvolumeclaims-pvc)

### "How do Docker, containerd, and Kubernetes relate?"
→ [ARCHITECTURE.md](ARCHITECTURE.md#container-runtime-docker-vs-containerd)

### "How does my data flow through the system?"
→ [ARCHITECTURE.md](ARCHITECTURE.md#etl-data-flow-extract-transform-load)

### "What does `ss -tlnp` show? Why don't NodePorts appear?"
→ [COMMANDS.md](COMMANDS.md#ss--tlnp)

### "How do I debug a pod crash?"
→ [DEBUGGING.md](DEBUGGING.md)

### "How do I connect to EC2 or run commands on it?"
→ [COMMANDS.md](COMMANDS.md#ssh-remote-access) or [DEBUGGING.md](DEBUGGING.md)

### "How do I push a Docker image to ECR?"
→ [ECR_SETUP.md](ECR_SETUP.md)

### "What Kubernetes commands do I need to know?"
→ [KUBECTL_COMMANDS.md](KUBECTL_COMMANDS.md) or [COMMANDS.md](COMMANDS.md#kubernetes-operations)

### "What was that incident with Airflow? What happened?"
→ [FIXES_AIRFLOW_2026-03-30.md](FIXES_AIRFLOW_2026-03-30.md)

---

## 📖 Documentation Organization

```
stock_live_data/
├── README.md                   ← START HERE (entry point)
├── OVERVIEW.md                 ← Comprehensive setup guide
├── infra_local.md              ← Secrets (gitignored)
│
└── docs/
    ├── INDEX.md                ← You are here
    ├── ARCHITECTURE.md         ← System design & concepts
    ├── GLOSSARY.md             ← Definitions & terms
    ├── COMMANDS.md             ← Command reference
    ├── DEBUGGING.md            ← Systematic troubleshooting
    ├── TROUBLESHOOTING.md      ← Specific solutions
    ├── ECR_SETUP.md            ← AWS image registry
    ├── FIXES_AIRFLOW_2026-03-30.md ← Incident analysis
    ├── STATUS_2026-03-30.md    ← Operational snapshot
    ├── CHANGELOG.md            ← History of changes
    ├── KUBECTL_COMMANDS.md     ← Kubernetes CLI reference
    └── refactor-ecr-migration.md ← Why we changed container setup
```

---

## 🔗 Cross-References

Each documentation file includes a "Quick Navigation" section at the top with links to related guides. Use these to jump between related topics.

---

## 📝 Contributing to Documentation

When adding documentation:
1. Use clear section headings (H2, H3)
2. Add code examples where helpful
3. Link to related guides
4. Define abbreviations in [GLOSSARY.md](GLOSSARY.md)
5. Reference [COMMANDS.md](COMMANDS.md) for command examples

---

**Last updated:** 2026-03-30
