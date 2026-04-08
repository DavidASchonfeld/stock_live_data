# Alerting stub — ready for Slack/PagerDuty integration in future steps
# Currently no-op; infrastructure in place for on_failure/on_retry/on_success callbacks

def on_failure_alert(context):
    """Task failure callback — currently a stub. TODO: wire to Slack/PagerDuty"""
    pass

def on_retry_alert(context):
    """Task retry callback — currently a stub. TODO: wire to Slack/PagerDuty"""
    pass

def on_success_alert(context):
    """Task success callback — currently a stub. TODO: wire to Slack/PagerDuty"""
    pass
