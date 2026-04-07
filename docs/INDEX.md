# Documentation Index

Complete navigation guide for all project documentation.

---

## Quick Start

1. **[plain-english/](plain-english/README.md)** — Non-technical guide: where code lives, what pods are, how bugs were fixed
2. **[README.md](../README.md)** — Project overview, architecture, tech stack
3. **[OVERVIEW.md](../OVERVIEW.md)** — Local dev setup, production deploy, K8s namespaces

---

## Planning & Roadmap

| Document | What it covers |
|----------|---------------|
| [BACKLOG.md](BACKLOG.md) | Ordered checklist: t3.large go-live, Snowflake migration, Kafka setup |

---

## Architecture (How the system works and fails)

| Document | What it covers |
|----------|---------------|
| [SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md) | K3s, containerd, pods, services, PVs, ETL data flow, Helm |
| [FAILURE_MODE_MAP.md](architecture/FAILURE_MODE_MAP.md) | Top failure modes per component, symptoms, root causes, blast radius |
| [COMPONENT_INTERACTIONS.md](architecture/COMPONENT_INTERACTIONS.md) | Dependency graph, blast radius analysis, cascade failure chains |
| [DATA_FLOW.md](architecture/DATA_FLOW.md) | Validation gates at each pipeline stage, XCom transport risks |

---

## Operations (Running and maintaining the system)

| Document | What it covers |
|----------|---------------|
| [RUNBOOKS.md](operations/RUNBOOKS.md) | Index of 18 step-by-step playbooks (split into [individual files](operations/runbooks/)) |
| [PREVENTION_CHECKLIST.md](operations/PREVENTION_CHECKLIST.md) | Pre-deploy, post-deploy, weekly health, DAG authoring checklists |
| [DEBUGGING.md](operations/DEBUGGING.md) | Systematic debugging approach, diagnostic sequences, common issues |
| [TROUBLESHOOTING.md](operations/TROUBLESHOOTING.md) | Specific issue solutions: DAG discovery, PV mismatch, staleness, deploy |

---

## Infrastructure (K3s, AWS, storage)

| Document | What it covers |
|----------|---------------|
| [K3S_RISKS.md](infrastructure/K3S_RISKS.md) | Single-node tradeoffs, containerd vs Docker, Helm state, security |
| [PERSISTENCE.md](infrastructure/PERSISTENCE.md) | PV/PVC deep dive: hostPath risks, filesystem cache, debugging |
| [EC2_SIZING.md](infrastructure/EC2_SIZING.md) | EC2 instance sizing: RAM table, t3.large verdict, Kafka heap tuning |
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
| [CHANGELOG.md](incidents/CHANGELOG.md) | Recent fixes and changes (older entries in [_archive/](incidents/_archive/)) |
| [2026-04-07 incident](incidents/2026-04-07-airflow-imagepullbackoff-incident.md) | ImagePullBackOff: obsolete images, invalid YAML, resource caching |
| [2026-04-06 learnings](incidents/2026-04-06-airflow-3x-upgrade-learnings.md) | Airflow 3.x upgrade: 7 root causes, cascade failure, recovery |
| [2026-03-31/](incidents/2026-03-31/) | Stock DAG disappearance: config drift, processor cache staleness |
| [2026-03-30/](incidents/2026-03-30/) | Airflow infra fixes: PostgreSQL image, PV path, DB credentials |

---

## Plain English Guide (Non-technical)

The guide is split into self-contained parts in [plain-english/](plain-english/README.md):

| Part | Topic |
|------|-------|
| [01](plain-english/01-where-code-lives.md) | Where your code lives |
| [02](plain-english/02-pods-and-navigation.md) | Pods and navigation |
| [03](plain-english/03-files-and-tunnels.md) | File mounts and SSH tunnels |
| [04](plain-english/04-bugs-config-and-infra.md) | Bug history: config and infrastructure |
| [05](plain-english/05-bugs-upgrade-and-migration.md) | Bug history: upgrade and migration |
| [06](plain-english/06-quick-reference.md) | Common tasks quick reference |
| [07](plain-english/07-alerting.md) | Alerting and notifications |
| [08](plain-english/08-big-picture-and-sizing.md) | Big picture and EC2 sizing |
| [09](plain-english/09-dbt-and-roadmap.md) | dbt and Step 2 roadmap |

---

## Common Questions

| Question | Go to |
|----------|-------|
| "What is K3S and why do we use it?" | [architecture/SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md) |
| "What can go wrong with my pipeline?" | [architecture/FAILURE_MODE_MAP.md](architecture/FAILURE_MODE_MAP.md) |
| "How do I deploy code changes?" | [operations/runbooks/deploy-and-dag.md](operations/runbooks/deploy-and-dag.md) |
| "Something broke, how do I debug it?" | [operations/DEBUGGING.md](operations/DEBUGGING.md) |
| "How do PersistentVolumes work?" | [infrastructure/PERSISTENCE.md](infrastructure/PERSISTENCE.md) |
| "What happened on 2026-03-31?" | [incidents/2026-03-31/](incidents/2026-03-31/) |
| "How do I set up Snowflake?" | [operations/runbooks/setup-snowflake.md](operations/runbooks/setup-snowflake.md) |
| "How does alerting work?" | [plain-english/07-alerting.md](plain-english/07-alerting.md) |
