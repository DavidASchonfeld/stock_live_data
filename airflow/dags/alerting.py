"""
Alerting Module

Provides Airflow task callbacks (on_failure, on_retry) and a data staleness
checker. Sends notifications to Slack via webhook, falling back to log-only
mode when no webhook URL is configured.

Zero external dependencies — uses urllib.request (stdlib) for HTTP POST.
"""

import json
import urllib.request
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from file_logger import OutputTextWriter
from db_config import DB_USER, DB_PASSWORD, DB_NAME, DB_HOST
from alert_config import (
    SLACK_WEBHOOK_URL,
    STALENESS_THRESHOLD_HOURS_STOCKS,
    STALENESS_THRESHOLD_HOURS_WEATHER,
    ALERT_COOLDOWN_MINUTES,  # cooldown window for alert deduplication
)


def _get_writer() -> OutputTextWriter:
    """Create a log writer with K8s PVC path, falling back to /tmp."""
    try:
        return OutputTextWriter("/opt/airflow/out")
    except PermissionError:
        return OutputTextWriter("/tmp")


def _send_slack_message(text_msg: str) -> None:
    """Post a message to Slack via webhook. Falls back to print() if no URL configured."""
    if not SLACK_WEBHOOK_URL:
        # Log-only mode — no webhook configured
        print(f"[ALERT - log only] {text_msg}")
        return

    try:
        payload = json.dumps({"text": text_msg}).encode("utf-8")
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        # 10s timeout so a Slack outage doesn't hang the DAG
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        # Never crash a pipeline because Slack is down
        print(f"[ALERT - Slack send failed] {e} | Original message: {text_msg}")


# ── Alert Cooldown Helpers ───────────────────────────────────────────────────
# Use Airflow Variables (same store as VACATION_MODE) to track last-alert
# timestamps. Keys are visible and manually deletable under Admin → Variables.

def _alert_variable_key(dag_id: str, task_id: str) -> str:
    """Build the Airflow Variable key for last-alert timestamp of a DAG+task."""
    return f"alert_last_sent:{dag_id}:{task_id}"


def _should_send_alert(key: str, cooldown_minutes: int) -> bool:
    """Return True if no alert was sent for this key within the cooldown window."""
    from airflow.models import Variable  # local import — avoids top-level Airflow dependency at parse time
    try:
        raw = Variable.get(key, default_var=None)
    except Exception:
        return True  # if Variable store is unavailable, allow the alert rather than suppress it
    if raw is None:
        return True  # no prior alert recorded — always send the first one
    try:
        last_sent = datetime.fromisoformat(raw)
    except ValueError:
        return True  # corrupt timestamp — treat as never alerted
    return (datetime.now() - last_sent) >= timedelta(minutes=cooldown_minutes)


def _record_alert_sent(key: str) -> None:
    """Persist current timestamp as last-alert time for this key."""
    from airflow.models import Variable  # local import
    try:
        Variable.set(key, datetime.now().isoformat())
    except Exception as e:
        # Don't crash the callback if the Variable write fails
        print(f"[ALERT - failed to record alert state] {e}")


def _clear_alert_state(key: str) -> None:
    """Delete the Airflow Variable tracking last-alert time, resetting cooldown."""
    from airflow.models import Variable  # local import
    try:
        Variable.delete(key)
    except Exception:
        pass  # Variable may not exist if task never failed — not an error


def _should_send_staleness_recovery(key: str) -> bool:
    """Return True if there was a prior staleness alert that has now resolved."""
    from airflow.models import Variable  # local import
    try:
        return Variable.get(key, default_var=None) is not None
    except Exception:
        return False  # if Variable store is unavailable, skip recovery message


# ── Airflow Callbacks ────────────────────────────────────────────────────────
# These fire regardless of vacation mode. If a DAG fails during vacation
# (instead of being cleanly skipped), vacation mode itself is broken — alert.

def on_failure_alert(context: dict) -> None:
    """Airflow on_failure_callback: logs + sends Slack alert on task failure."""
    writer = _get_writer()

    dag_id = context.get("dag", {}).dag_id if context.get("dag") else "unknown"
    task_id = context.get("task_instance", {}).task_id if context.get("task_instance") else "unknown"
    execution_date = context.get("execution_date", "unknown")
    exception = context.get("exception", "No exception info")

    # Deduplicate: skip Slack if this DAG+task already alerted within the cooldown window
    key = _alert_variable_key(dag_id, task_id)
    if not _should_send_alert(key, ALERT_COOLDOWN_MINUTES):
        writer.print(f"[FAILURE ALERT - suppressed, within cooldown] {dag_id}.{task_id}")
        return

    msg = (
        f":red_circle: *Task Failed*\n"
        f"DAG: `{dag_id}` | Task: `{task_id}`\n"
        f"Execution: {execution_date}\n"
        f"Error: {exception}"
    )

    writer.print(f"[FAILURE ALERT] {msg}")
    _send_slack_message(msg)
    _record_alert_sent(key)  # record timestamp so subsequent failures within the window are suppressed


def on_retry_alert(context: dict) -> None:
    """Airflow on_retry_callback: logs + sends Slack warning on task retry."""
    writer = _get_writer()

    dag_id = context.get("dag", {}).dag_id if context.get("dag") else "unknown"
    task_id = context.get("task_instance", {}).task_id if context.get("task_instance") else "unknown"
    try_number = context.get("task_instance").try_number if context.get("task_instance") else "?"

    # Suppress retry alert if a failure alert was already sent within the cooldown window
    key = _alert_variable_key(dag_id, task_id)
    if not _should_send_alert(key, ALERT_COOLDOWN_MINUTES):
        writer.print(f"[RETRY ALERT - suppressed, failure already reported] {dag_id}.{task_id} attempt {try_number}")
        return

    msg = (
        f":large_yellow_circle: *Task Retrying*\n"
        f"DAG: `{dag_id}` | Task: `{task_id}`\n"
        f"Attempt: {try_number}"
    )

    writer.print(f"[RETRY ALERT] {msg}")
    _send_slack_message(msg)
    _record_alert_sent(key)  # record timestamp for the unlikely case a retry fires before a failure alert


def on_success_alert(context: dict) -> None:
    """Airflow on_success_callback: sends recovery message and clears alert state if task previously failed."""
    from airflow.models import Variable  # local import
    writer = _get_writer()

    dag_id = context.get("dag", {}).dag_id if context.get("dag") else "unknown"
    task_id = context.get("task_instance", {}).task_id if context.get("task_instance") else "unknown"

    # Only notify on recovery if a prior failure alert was recorded for this task
    key = _alert_variable_key(dag_id, task_id)
    try:
        if Variable.get(key, default_var=None) is None:
            return  # task never failed (or already recovered) — no recovery message needed
    except Exception:
        return  # if Variable store is unavailable, skip silently

    msg = (
        f":green_circle: *Task Recovered*\n"
        f"DAG: `{dag_id}` | Task: `{task_id}`\n"
        f"Task is now succeeding after a previous failure."
    )

    writer.print(f"[RECOVERY ALERT] {msg}")
    _send_slack_message(msg)
    _clear_alert_state(key)  # reset so the next failure starts a fresh cooldown cycle


# ── Data Staleness Checker ───────────────────────────────────────────────────

def check_data_staleness() -> None:
    """
    Query MAX timestamps from both tables and alert if data exceeds
    staleness thresholds. Called by the staleness monitoring DAG.
    """
    writer = _get_writer()
    writer.print(f"Staleness check started: {datetime.now()}")

    engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")
    alerts = []

    try:
        with engine.connect() as conn:
            # Check company_financials freshness (filed_date is a string like "2025-03-15")
            result = conn.execute(text("SELECT MAX(filed_date) FROM company_financials"))
            latest_filed = result.scalar()
            staleness_key_stocks = "alert_last_sent:staleness:company_financials"  # cooldown key for this table

            if latest_filed:
                latest_dt = datetime.strptime(str(latest_filed), "%Y-%m-%d")
                age_hours = (datetime.now() - latest_dt).total_seconds() / 3600
                writer.print(f"  company_financials: latest filed_date={latest_filed}, age={age_hours:.1f}h, threshold={STALENESS_THRESHOLD_HOURS_STOCKS}h")

                if age_hours > STALENESS_THRESHOLD_HOURS_STOCKS:
                    # Only alert if outside the cooldown window
                    if _should_send_alert(staleness_key_stocks, ALERT_COOLDOWN_MINUTES):
                        alerts.append(
                            f":clock1: *Stale Data: company_financials*\n"
                            f"Latest filing: {latest_filed} ({age_hours:.0f}h ago)\n"
                            f"Threshold: {STALENESS_THRESHOLD_HOURS_STOCKS}h"
                        )
                        _record_alert_sent(staleness_key_stocks)  # suppress repeat alerts within window
                    else:
                        writer.print("  [STALENESS ALERT - suppressed, within cooldown] company_financials")
                else:
                    # Table is fresh — send a recovery message if we previously alerted on it
                    if _should_send_staleness_recovery(staleness_key_stocks):
                        recovery_msg = (
                            f":green_circle: *Staleness Resolved: company_financials*\n"
                            f"Latest filing: {latest_filed} — now within threshold."
                        )
                        writer.print(f"[STALENESS RECOVERY] {recovery_msg}")
                        _send_slack_message(recovery_msg)
                        _clear_alert_state(staleness_key_stocks)  # reset cooldown after recovery
            else:
                # No rows at all — treat the same as stale, with cooldown
                if _should_send_alert(staleness_key_stocks, ALERT_COOLDOWN_MINUTES):
                    alerts.append(":clock1: *No data in company_financials table*")
                    _record_alert_sent(staleness_key_stocks)
                else:
                    writer.print("  [STALENESS ALERT - suppressed, within cooldown] company_financials (empty)")

            # Check weather_hourly freshness (imported_at is ISO format like "2025-03-31T14:30:00")
            result = conn.execute(text("SELECT MAX(imported_at) FROM weather_hourly"))
            latest_imported = result.scalar()
            staleness_key_weather = "alert_last_sent:staleness:weather_hourly"  # cooldown key for this table

            if latest_imported:
                latest_dt = datetime.fromisoformat(str(latest_imported))
                age_hours = (datetime.now() - latest_dt).total_seconds() / 3600
                writer.print(f"  weather_hourly: latest imported_at={latest_imported}, age={age_hours:.1f}h, threshold={STALENESS_THRESHOLD_HOURS_WEATHER}h")

                if age_hours > STALENESS_THRESHOLD_HOURS_WEATHER:
                    # Only alert if outside the cooldown window
                    if _should_send_alert(staleness_key_weather, ALERT_COOLDOWN_MINUTES):
                        alerts.append(
                            f":clock1: *Stale Data: weather_hourly*\n"
                            f"Latest import: {latest_imported} ({age_hours:.1f}h ago)\n"
                            f"Threshold: {STALENESS_THRESHOLD_HOURS_WEATHER}h"
                        )
                        _record_alert_sent(staleness_key_weather)  # suppress repeat alerts within window
                    else:
                        writer.print("  [STALENESS ALERT - suppressed, within cooldown] weather_hourly")
                else:
                    # Table is fresh — send a recovery message if we previously alerted on it
                    if _should_send_staleness_recovery(staleness_key_weather):
                        recovery_msg = (
                            f":green_circle: *Staleness Resolved: weather_hourly*\n"
                            f"Latest import: {latest_imported} — now within threshold."
                        )
                        writer.print(f"[STALENESS RECOVERY] {recovery_msg}")
                        _send_slack_message(recovery_msg)
                        _clear_alert_state(staleness_key_weather)  # reset cooldown after recovery
            else:
                # No rows at all — treat the same as stale, with cooldown
                if _should_send_alert(staleness_key_weather, ALERT_COOLDOWN_MINUTES):
                    alerts.append(":clock1: *No data in weather_hourly table*")
                    _record_alert_sent(staleness_key_weather)
                else:
                    writer.print("  [STALENESS ALERT - suppressed, within cooldown] weather_hourly (empty)")

    except SQLAlchemyError as e:
        writer.print(f"  Database error during staleness check: {e}")
        raise

    # Send one Slack message per stale table (only those that passed the cooldown gate)
    for alert_msg in alerts:
        writer.print(f"[STALENESS ALERT] {alert_msg}")
        _send_slack_message(alert_msg)

    if not alerts:
        writer.print("  All tables within freshness thresholds — no alerts.")
