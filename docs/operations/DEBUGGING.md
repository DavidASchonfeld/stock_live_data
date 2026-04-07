# Debugging Guide — Stock Live Data

A learning-oriented reference for debugging this project's stack: **K3s + Airflow + Flask on EC2**.

---

## Sections

| Guide | What's inside |
|-------|---------------|
| [Approach & Mental Model](debugging/approach.md) | Three-layer traffic path, namespaces, common gotchas |
| [Diagnostic Sequences](debugging/diagnostic-sequences.md) | Step-by-step diagnostic commands, Airflow 3.x gotchas, log reading, health checks |
| [Common Issues A-I](debugging/common-issues-1.md) | PermissionError, endpoints `<none>`, ImagePullBackOff, Init:0/1, DAG paused, DB access denied, UI unreachable, empty dashboard, deprecation warnings, CrashLoopBackOff |
| [Common Issues J-N](debugging/common-issues-2.md) | rsync mkdir failure, weather DAG load errors, OOMKill static assets, 404 UI bug, pymysql missing |

---

## Related Docs

- [COMMANDS.md](../reference/COMMANDS.md) — What `ss -tlnp`, `kubectl`, `rsync` do
- [SYSTEM_OVERVIEW.md](../architecture/SYSTEM_OVERVIEW.md) — System architecture
- [GLOSSARY.md](../reference/GLOSSARY.md) — Term definitions (iptables, XCom, inode, etc.)
- [FAILURE_MODE_MAP.md](../architecture/FAILURE_MODE_MAP.md) — Failure mode catalog
- [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md) — Prevention checklists
