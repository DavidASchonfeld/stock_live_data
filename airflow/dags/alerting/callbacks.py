"""
Airflow Task Callbacks

These fire regardless of vacation mode. If a DAG fails during vacation
(instead of being cleanly skipped), vacation mode itself is broken — alert.
"""

from file_logger import OutputTextWriter
from shared.config import ALERT_COOLDOWN_MINUTES
from alerting.notifier import _send_slack_message
from alerting.cooldown import (
    _alert_variable_key,
    _should_send_alert,
    _record_alert_sent,
    _clear_alert_state,
)


def _get_writer() -> OutputTextWriter:
    """Create a log writer with K8s PVC path, falling back to /tmp."""
    try:
        return OutputTextWriter("/opt/airflow/out")
    except PermissionError:
        return OutputTextWriter("/tmp")


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
        writer.log(f"[FAILURE ALERT - suppressed, within cooldown] {dag_id}.{task_id}")
        return

    msg = (
        f":red_circle: *Task Failed*\n"
        f"DAG: `{dag_id}` | Task: `{task_id}`\n"
        f"Execution: {execution_date}\n"
        f"Error: {exception}"
    )

    writer.log(f"[FAILURE ALERT] {msg}")
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
        writer.log(f"[RETRY ALERT - suppressed, failure already reported] {dag_id}.{task_id} attempt {try_number}")
        return

    msg = (
        f":large_yellow_circle: *Task Retrying*\n"
        f"DAG: `{dag_id}` | Task: `{task_id}`\n"
        f"Attempt: {try_number}"
    )

    writer.log(f"[RETRY ALERT] {msg}")
    _send_slack_message(msg)
    _record_alert_sent(key)  # record timestamp for the unlikely case a retry fires before a failure alert


def on_success_alert(context: dict) -> None:
    """Airflow on_success_callback: sends recovery message and clears alert state if task previously failed."""
    from airflow.sdk import Variable  # local import
    writer = _get_writer()

    dag_id = context.get("dag", {}).dag_id if context.get("dag") else "unknown"
    task_id = context.get("task_instance", {}).task_id if context.get("task_instance") else "unknown"

    # Only notify on recovery if a prior failure alert was recorded for this task
    key = _alert_variable_key(dag_id, task_id)
    try:
        if Variable.get(key, default=None) is None:  # Airflow 3.x: default_var renamed to default
            return  # task never failed (or already recovered) — no recovery message needed
    except Exception:
        return  # if Variable store is unavailable, skip silently

    msg = (
        f":green_circle: *Task Recovered*\n"
        f"DAG: `{dag_id}` | Task: `{task_id}`\n"
        f"Task is now succeeding after a previous failure."
    )

    writer.log(f"[RECOVERY ALERT] {msg}")
    _send_slack_message(msg)
    _clear_alert_state(key)  # reset so the next failure starts a fresh cooldown cycle
