# Alerting — Slack webhook callbacks for on_failure, on_retry, on_success
# Reads SLACK_WEBHOOK_URL from Airflow Variable; silently no-ops if unset

import requests
from airflow.models import Variable

# Only fire success alert on the final task in each DAG
_SUCCESS_TASKS = {"dbt_test"}


def _post_slack(webhook_url: str, text: str) -> None:
    """POST a plain-text message to a Slack webhook."""
    requests.post(webhook_url, json={"text": text}, timeout=5)


def on_failure_alert(context):
    """Task failure callback — posts to Slack if SLACK_WEBHOOK_URL Variable is set."""
    url = Variable.get("SLACK_WEBHOOK_URL", default_var=None)
    if not url:
        return
    ti = context["task_instance"]
    _post_slack(url, f":red_circle: *FAILURE* `{ti.dag_id}` / `{ti.task_id}`\nRun: {ti.run_id}\nLog: {ti.log_url}")


def on_retry_alert(context):
    """Task retry callback — posts to Slack if SLACK_WEBHOOK_URL Variable is set."""
    url = Variable.get("SLACK_WEBHOOK_URL", default_var=None)
    if not url:
        return
    ti = context["task_instance"]
    _post_slack(url, f":yellow_circle: *RETRY* `{ti.dag_id}` / `{ti.task_id}`\nRun: {ti.run_id}\nLog: {ti.log_url}")


def on_success_alert(context):
    """Final-task success callback — only fires for tasks in _SUCCESS_TASKS."""
    ti = context["task_instance"]
    if ti.task_id not in _SUCCESS_TASKS:
        return
    url = Variable.get("SLACK_WEBHOOK_URL", default_var=None)
    if not url:
        return
    _post_slack(url, f":large_green_circle: *SUCCESS* `{ti.dag_id}` / `{ti.task_id}`\nRun: {ti.run_id}\nLog: {ti.log_url}")
