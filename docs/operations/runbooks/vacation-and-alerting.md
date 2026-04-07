# Runbooks 11–12: Vacation Mode + Slack Alerting

> Part of the [Runbooks Index](../RUNBOOKS.md).

---

## 11. Enable / Disable Vacation Mode

**When:** You're leaving and want to stop all DAGs from calling external APIs, or you're back and want to resume.

### Two-layer protection

| Layer | Mechanism | Where to set |
|-------|-----------|--------------|
| Primary | Airflow native **pause** | Airflow UI toggle |
| Guard | `VACATION_MODE` **Airflow Variable** | Admin → Variables |

Always enable **both** for maximum safety.

### Enable vacation mode

1. **Set the Airflow Variable:** Airflow UI → Admin → Variables → "+" → Key: `VACATION_MODE`, Value: `true`
2. **Pause both DAGs:** Airflow UI → DAGs list → click the toggle for each DAG
3. **Verify:** `ssh ec2-stock kubectl exec -n airflow-my-namespace airflow-scheduler-0 -- airflow variables get VACATION_MODE` → should print `true`

**What happens:** Any scheduled run calls `check_vacation_mode()` in `extract()` and raises `AirflowSkipException`. All tasks are marked **Skipped** (not Failed). No API calls or DB writes happen.

### Test it works

Trigger a manual run — all tasks should show **Skipped** (pink badge). If any show **Failed**, check that the Variable value is exactly `true` (lowercase, no spaces).

### Audit past runs

Every run logs `VACATION_MODE = true/false` in the `extract` task log. Check: Airflow UI → DAGs → click run → extract → Log → search for `VACATION_MODE =`.

### Disable vacation mode

1. Airflow UI → Admin → Variables → `VACATION_MODE` → change to `false` (or delete it)
2. Unpause both DAGs
3. Trigger manual runs to confirm everything works

**Success criteria:** Both runs complete with `state: success`, new rows appear in the database.

---

## 12. Configure Slack Alerting

> **Current status:** The alerting infrastructure is fully built but not connected to a Slack workspace. Running in log-only mode.

**When:** Setting up Slack notifications for DAG failures, retries, and data staleness.

### Create Slack Webhook

1. Go to `api.slack.com/apps` → **Create New App** → **From scratch**
2. Name it (e.g., "Stock Pipeline Alerts"), select your workspace
3. **Incoming Webhooks** → toggle **On** → **Add New Webhook to Workspace**
4. Choose the alert channel → **Allow**
5. Copy the webhook URL

### Configure Locally

Add to your `.env` file: `SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx`

Without this variable, alerting runs in **log-only mode**.

### Configure in Kubernetes (production)

```bash
ssh ec2-stock
kubectl create secret generic db-credentials \
  -n airflow-my-namespace \
  --from-literal=DB_USER=airflow_user \
  --from-literal=DB_PASSWORD=<DB_PASSWORD> \
  --from-literal=DB_NAME=database_one \
  --from-literal=DB_HOST=<MARIADB_PRIVATE_IP> \
  --from-literal=EDGAR_CONTACT_EMAIL=davedevportfolio@gmail.com \
  --from-literal=SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../xxx \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart pods to pick up the new secret
kubectl delete pod airflow-scheduler-0 -n airflow-my-namespace
kubectl delete pod -l component=dag-processor -n airflow-my-namespace
sleep 60
kubectl get pods -n airflow-my-namespace
```

### Staleness Thresholds

| Variable | Default | Meaning |
|----------|---------|---------|
| `STALENESS_THRESHOLD_HOURS_STOCKS` | 168 (7 days) | Alert if no recent stock data |
| `STALENESS_THRESHOLD_HOURS_WEATHER` | 2 | Alert if no recent weather data |
| `ALERT_COOLDOWN_MINUTES` | 60 | Minimum minutes between repeated alerts per failure |

Alert state is stored as Airflow Variables (`alert_last_sent:*`) — visible under Admin → Variables. Delete a variable to immediately re-arm that alert.

**Recovery notifications:** When a failing task succeeds again, a "Task Recovered" message is sent and cooldown state is cleared.

### Vacation Mode Behavior

- **Failure/retry alerts always fire** during vacation — if a DAG fails instead of skipping, vacation mode itself is broken
- **Staleness alerts are silenced** — stale data is expected when pipelines are paused

**Success criteria:** Slack message appears when a task fails or data exceeds the staleness threshold.
