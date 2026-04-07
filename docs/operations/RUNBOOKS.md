# Operational Runbooks

Step-by-step playbooks for common operations. Each runbook is a complete procedure.

**Related docs:**
- Pre-flight checklists → [PREVENTION_CHECKLIST.md](PREVENTION_CHECKLIST.md)
- Something went wrong → [troubleshooting/](troubleshooting/)
- Why these steps matter → [../architecture/COMPONENT_INTERACTIONS.md](../architecture/COMPONENT_INTERACTIONS.md)

---

## Table of Contents

| # | Runbook | File |
|---|---------|------|
| 1 | Deploy Code Changes | [runbooks/deploy-and-dag.md](runbooks/deploy-and-dag.md) |
| 2 | Add a New DAG | [runbooks/deploy-and-dag.md](runbooks/deploy-and-dag.md) |
| 3 | Rotate Database Credentials | [runbooks/credentials-and-rollback.md](runbooks/credentials-and-rollback.md) |
| 4 | Rollback a Bad Helm Upgrade | [runbooks/credentials-and-rollback.md](runbooks/credentials-and-rollback.md) |
| 5 | Recover from Total Cluster Outage | [runbooks/recovery-and-backfill.md](runbooks/recovery-and-backfill.md) |
| 6 | Backfill Missing Data | [runbooks/recovery-and-backfill.md](runbooks/recovery-and-backfill.md) |
| 7 | Update Flask Dashboard Image | [runbooks/dashboard-and-ip.md](runbooks/dashboard-and-ip.md) |
| 8 | Change Working Location (IP Update) | [runbooks/dashboard-and-ip.md](runbooks/dashboard-and-ip.md) |
| 9 | Investigate Stale Data | [runbooks/dashboard-and-ip.md](runbooks/dashboard-and-ip.md) |
| 10 | Add a New API Data Source | [runbooks/dashboard-and-ip.md](runbooks/dashboard-and-ip.md) |
| 11 | Enable / Disable Vacation Mode | [runbooks/vacation-and-alerting.md](runbooks/vacation-and-alerting.md) |
| 12 | Configure Slack Alerting | [runbooks/vacation-and-alerting.md](runbooks/vacation-and-alerting.md) |
| 13 | Migrate EC2 to a New Region | [runbooks/migrate-region.md](runbooks/migrate-region.md) |
| 14 | Set Up and Activate Snowflake | [runbooks/setup-snowflake.md](runbooks/setup-snowflake.md) |
| 15 | Migrate EC2 from AL2023 to Ubuntu 24.04 | [runbooks/migrate-ubuntu.md](runbooks/migrate-ubuntu.md) |
| 16 | Fix DAG Parse Errors / ERR_NETWORK | [runbooks/dag-fixes-and-updates.md](runbooks/dag-fixes-and-updates.md) |
| 17 | Fix Static Assets Failing (OOMKill) | [runbooks/dag-fixes-and-updates.md](runbooks/dag-fixes-and-updates.md) |
| 18 | Apply Ubuntu OS Security Updates | [runbooks/dag-fixes-and-updates.md](runbooks/dag-fixes-and-updates.md) |
