# Plain English Guide — How This Project Actually Works

This guide explains the project in simple, non-technical language. Each part is a self-contained topic you can read on its own.

| Part | What it covers |
|------|---------------|
| [01 — Where Code Lives](01-where-code-lives.md) | Your Mac, EC2, and pods — the three places your code exists |
| [02 — Pods and Navigation](02-pods-and-navigation.md) | What pods are, namespaces, how to look inside them |
| [03 — Files and Tunnels](03-files-and-tunnels.md) | How files get from your Mac into pods, SSH tunnels |
| [04 — Bug History (Part 1)](04-bugs-config-and-infra.md) | Bugs 1–8: config drift, cache staleness, PV mismatch, PostgreSQL image, probes |
| [05 — Bug History (Part 2)](05-bugs-upgrade-and-migration.md) | Bugs 9–16: OOM, deploy errors, upgrade cascade, selector mismatch |
| [06 — Quick Reference](06-quick-reference.md) | Common tasks: deploy, check DAGs, trigger pipelines, check data |
| [07 — Alerting](07-alerting.md) | How notifications work, Slack setup, cooldown, vacation mode |
| [08 — Sizing and Architecture](08-big-picture-and-sizing.md) | The big picture, EC2 sizing, resource limits |
| [09 — dbt and Roadmap](09-dbt-and-roadmap.md) | What dbt is, Step 2 plan (Snowflake → dbt → Kafka) |
| [10 — Snowflake Write Failure](10-snowflake-write-failure-explained.md) | April 7 incident: why weather DAG failed to write to Snowflake and how we fixed it |
