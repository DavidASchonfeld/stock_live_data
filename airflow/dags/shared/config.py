import os

from dotenv import load_dotenv  # reads .env for local dev; no-op in production

load_dotenv()

# ── Database ──────────────────────────────────────────────────────────────────
# Credentials come from environment variables — this file never contains secrets.
# Local dev:   set values in a .env file at the repo root (gitignored)
# Production:  set values in a Kubernetes Secret (see k8s-db-secret.yaml template)
DB_USER     = os.environ.get("DB_USER",     "airflow_user")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME     = os.environ.get("DB_NAME",     "database_one")
DB_HOST     = os.environ.get("DB_HOST",     "localhost")

# ── Alerting ──────────────────────────────────────────────────────────────────
# Slack webhook URL — empty string = log-only mode (no Slack messages sent)
# Local dev:   set in .env file at the repo root
# Production:  add to Kubernetes Secret alongside DB_USER/DB_PASSWORD/etc.
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# Hours before data is considered stale (triggers alert)
# SEC EDGAR filings are weekly, so 168h (7 days) is a reasonable default
STALENESS_THRESHOLD_HOURS_STOCKS = int(os.environ.get("STALENESS_THRESHOLD_HOURS_STOCKS", "168"))
# Open-Meteo updates hourly and we pull every 5 min, so 2h means something is broken
STALENESS_THRESHOLD_HOURS_WEATHER = int(os.environ.get("STALENESS_THRESHOLD_HOURS_WEATHER", "2"))

# Minutes before a repeat alert is allowed for the same DAG+task or stale table (prevents spam)
ALERT_COOLDOWN_MINUTES = int(os.environ.get("ALERT_COOLDOWN_MINUTES", "60"))

# ── Local paths ───────────────────────────────────────────────────────────────
# Used as the fallback default path in OutputTextWriter for local dev.
# Production always passes /opt/airflow/out explicitly, so this default is never used there.
# Local dev:   set LOCAL_LOG_PATH in your .env file to your local logs directory
LOCAL_LOG_PATH = os.environ.get("LOCAL_LOG_PATH", "/tmp/airflow_logs")
