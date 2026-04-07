# Part 7: Alerting — Getting Notified When Things Break

> Part of the [Plain English Guide](README.md)

---

## What is alerting?

When a pipeline task fails or your data goes stale, the system sends you a notification. Without alerting, you'd only find out something was broken when you manually opened the Airflow UI or checked the dashboard — which could be hours or days later.

## How it works

When a task fails, your pipeline automatically calls a function (`on_failure_alert`) that:
1. Writes the failure to the PVC log file (always)
2. Sends a message to Slack (if you set up a webhook URL)

A separate monitoring DAG (`Data_Staleness_Monitor`) runs every 30 minutes. It checks how old the latest data is in each table and alerts you if data hasn't been updated in too long.

## Preventing notification spam (cooldown)

Your DAGs run frequently. Without protection, a single broken task could send you 12+ Slack messages per hour. That's alert fatigue: so many notifications that you start ignoring them.

**The cooldown system works like a "don't call me again for an hour" rule:**

- First time a task fails → you get a Slack message immediately
- Same task fails again within 60 minutes → suppressed (logged but not sent)
- Task finally succeeds → you get one "Task Recovered" message, clock resets

**Same rule applies to retries and staleness checks.** One alert per issue, not one per occurrence.

**How it stores state:** The cooldown timer is saved as an Airflow Variable (same system as vacation mode). You can see these in the Airflow UI under Admin → Variables. If you want to be notified again immediately, delete the relevant variable.

## What is Slack?

**Slack is a messaging app** — like iMessage or WhatsApp but for teams. You install it on your Mac or phone, create a free account, and it gives you channels (chat rooms). Alerts appear as regular notifications.

**You do not need to provide your email address.** Slack is its own system. Alerts are not sent via email. You receive messages in the Slack app, not in the terminal.

## What is a Slack webhook?

A **webhook** is a secret URL that Slack gives you. When your pipeline sends a request to that URL with a message, Slack delivers it to a channel:

```
Pipeline task fails → Python POSTs to https://hooks.slack.com/services/... → Slack shows message → phone notification
```

## Do you need to set up Slack?

No — it's optional. Without configuration, the alerting system runs in **log-only mode**: failures are still logged to the PVC files on EC2, you just won't get push notifications.

To get actual notifications, set up a free Slack account + webhook URL. See the operations runbook for step-by-step setup.

> **Current status:** The alerting infrastructure is fully built but not connected to a Slack workspace. Running in log-only mode.

## Vacation mode and alerts

- **Failure/retry alerts still fire** during vacation — if a DAG fails instead of cleanly skipping, vacation mode itself is broken, which is worth knowing
- **Staleness alerts are silenced** — stale data is expected when you've intentionally paused pipelines

## Files added for alerting

| File | What it does |
|------|-------------|
| `airflow/dags/alerting/` | Alert package — Slack, PVC logging, staleness checking, cooldown |
| `airflow/dags/shared/config.py` | Central config: webhook URL, staleness thresholds |
| `airflow/dags/dag_staleness_check.py` | New DAG that runs every 30 minutes, checks data freshness |
