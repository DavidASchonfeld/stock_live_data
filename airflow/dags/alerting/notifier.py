import json
import urllib.request

from shared.config import SLACK_WEBHOOK_URL


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
