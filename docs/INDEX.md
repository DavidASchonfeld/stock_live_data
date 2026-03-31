# Documentation Index

Complete navigation guide for all project documentation.

---

## Quick Start

1. **README.md** (project root) — Overview and entry point
2. **OVERVIEW.md** (project root) — Setup guide, read before deploying
3. **[architecture/SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md)** — How K3s, Airflow, MariaDB, and Flask connect

---

## Architecture (How the system works and fails)

| Document | What it covers |
|----------|---------------|
| [SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md) | K3s, containerd, pods, services, PVs, ETL data flow, Helm |
| [FAILURE_MODE_MAP.md](architecture/FAILURE_MODE_MAP.md) | Top 5 failure modes per component, symptoms, root causes, blast radius |
| [COMPONENT_INTERACTIONS.md](architecture/COMPONENT_INTERACTIONS.md) | Dependency graph, blast radius analysis, cascade failure chains |
| [DATA_FLOW.md](architecture/DATA_FLOW.md) | Validation gates at each pipeline stage, XCom transport risks |

---

## Operations (Running and maintaining the system)

| Document | What it covers |
|----------|---------------|
| [RUNBOOKS.md](operations/RUNBOOKS.md) | Step-by-step playbooks: deploy, add DAG, rotate secrets, rollback, recover |
| [PREVENTION_CHECKLIST.md](operations/PREVENTION_CHECKLIST.md) | Pre-deploy, post-deploy, weekly health, DAG authoring checklists |
| [DEBUGGING.md](operations/DEBUGGING.md) | Systematic debugging approach, diagnostic sequences, common issues A-K |
| [TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) | Specific issue solutions: DAG discovery, PV mismatch, staleness, deploy failures |

---

## Infrastructure (K3s, AWS, storage)

| Document | What it covers |
|----------|---------------|
| [K3S_RISKS.md](infrastructure/K3S_RISKS.md) | Single-node tradeoffs, containerd vs Docker, Helm state, resource contention, security |
| [PERSISTENCE.md](infrastructure/PERSISTENCE.md) | PV/PVC deep dive: hostPath risks, filesystem cache, reclaim policy, debugging |
| [ECR_SETUP.md](infrastructure/ECR_SETUP.md) | AWS ECR configuration, image push workflow, authentication |
| [refactor-ecr-migration.md](infrastructure/refactor-ecr-migration.md) | Why containerd + ECR replaced Docker mode |

---

## Reference (Commands, terms, tools)

| Document | What it covers |
|----------|---------------|
| [COMMANDS.md](reference/COMMANDS.md) | Shell command explanations: ss, rsync, kubectl, docker |
| [KUBECTL_COMMANDS.md](reference/KUBECTL_COMMANDS.md) | Kubernetes CLI reference |
| [GLOSSARY.md](reference/GLOSSARY.md) | Technical terms: SMA, ETL, DAG, PV, PVC, K3S, XCom, etc. |

---

## Incidents (Historical record)

| Document | What it covers |
|----------|---------------|
| [CHANGELOG.md](incidents/CHANGELOG.md) | All fixes and changes over time |
| [2026-03-31/](incidents/2026-03-31/) | Stock DAG disappearance: config drift, processor cache staleness, root cause analysis |
| [2026-03-30/](incidents/2026-03-30/) | Airflow infra fixes: PostgreSQL image deletion, PV path mismatch, DB credential injection |

---

## File Tree

```
docs/
├── INDEX.md                          ← You are here
│
├── architecture/
│   ├── SYSTEM_OVERVIEW.md            ← System design & concepts (was ARCHITECTURE.md)
│   ├── FAILURE_MODE_MAP.md           ← Proactive failure catalog
│   ├── COMPONENT_INTERACTIONS.md     ← Dependency graph & cascade failures
│   └── DATA_FLOW.md                  ← Validation gates per pipeline stage
│
├── operations/
│   ├── RUNBOOKS.md                   ← Step-by-step operational playbooks
│   ├── PREVENTION_CHECKLIST.md       ← Checklists for deploy, infra, secrets
│   ├── DEBUGGING.md                  ← Systematic debugging approach
│   └── TROUBLESHOOTING.md           ← Specific issue solutions
│
├── infrastructure/
│   ├── K3S_RISKS.md                  ← Hidden K3s complexity
│   ├── PERSISTENCE.md               ← PV/PVC deep dive
│   ├── ECR_SETUP.md                  ← AWS ECR setup
│   └── refactor-ecr-migration.md     ← ECR migration rationale
│
├── reference/
│   ├── COMMANDS.md                   ← Shell command reference
│   ├── KUBECTL_COMMANDS.md           ← Kubernetes CLI reference
│   └── GLOSSARY.md                   ← Technical terms
│
└── incidents/
    ├── CHANGELOG.md                  ← History of all changes
    ├── 2026-03-30/
    │   ├── FIXES_AIRFLOW.md          ← PostgreSQL + PV + secrets incident
    │   └── STATUS.md                 ← Operational snapshot
    └── 2026-03-31/
        ├── INVESTIGATION.md          ← Stock DAG persistence investigation
        ├── ROOT_CAUSE.md             ← Root cause analysis
        ├── STATUS.md                 ← Operational snapshot
        └── SESSION_SUMMARY.md        ← Full session summary
```

---

## Common Questions

| Question | Go to |
|----------|-------|
| "What is K3S and why do we use it?" | [architecture/SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md#why-k3s) |
| "What can go wrong with my pipeline?" | [architecture/FAILURE_MODE_MAP.md](architecture/FAILURE_MODE_MAP.md) |
| "If MariaDB goes down, what else breaks?" | [architecture/COMPONENT_INTERACTIONS.md](architecture/COMPONENT_INTERACTIONS.md) |
| "Where should I validate data?" | [architecture/DATA_FLOW.md](architecture/DATA_FLOW.md) |
| "How do I deploy code changes?" | [operations/RUNBOOKS.md](operations/RUNBOOKS.md#1-deploy-code-changes) |
| "What should I check before deploying?" | [operations/PREVENTION_CHECKLIST.md](operations/PREVENTION_CHECKLIST.md) |
| "Something broke, how do I debug it?" | [operations/DEBUGGING.md](operations/DEBUGGING.md) |
| "What are the hidden risks of K3s?" | [infrastructure/K3S_RISKS.md](infrastructure/K3S_RISKS.md) |
| "How do PersistentVolumes actually work?" | [infrastructure/PERSISTENCE.md](infrastructure/PERSISTENCE.md) |
| "What happened on 2026-03-31?" | [incidents/2026-03-31/](incidents/2026-03-31/) |

---

**Last updated:** 2026-03-31
