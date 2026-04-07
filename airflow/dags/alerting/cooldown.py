"""
Alert Cooldown Helpers

Use Airflow Variables (same store as VACATION_MODE) to track last-alert
timestamps. Keys are visible and manually deletable under Admin → Variables.
"""

from datetime import datetime, timedelta


def _alert_variable_key(dag_id: str, task_id: str) -> str:
    """Build the Airflow Variable key for last-alert timestamp of a DAG+task."""
    return f"alert_last_sent:{dag_id}:{task_id}"


def _should_send_alert(key: str, cooldown_minutes: int) -> bool:
    """Return True if no alert was sent for this key within the cooldown window."""
    from airflow.sdk import Variable  # local import — avoids top-level Airflow dependency at parse time
    try:
        raw = Variable.get(key, default=None)  # Airflow 3.x: default_var renamed to default
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
    from airflow.sdk import Variable  # local import
    try:
        Variable.set(key, datetime.now().isoformat())
    except Exception as e:
        # Don't crash the callback if the Variable write fails
        print(f"[ALERT - failed to record alert state] {e}")


def _clear_alert_state(key: str) -> None:
    """Delete the Airflow Variable tracking last-alert time, resetting cooldown."""
    from airflow.sdk import Variable  # local import
    try:
        Variable.delete(key)
    except Exception:
        pass  # Variable may not exist if task never failed — not an error


def _should_send_staleness_recovery(key: str) -> bool:
    """Return True if there was a prior staleness alert that has now resolved."""
    from airflow.sdk import Variable  # local import
    try:
        return Variable.get(key, default=None) is not None  # Airflow 3.x: default_var renamed to default
    except Exception:
        return False  # if Variable store is unavailable, skip recovery message
