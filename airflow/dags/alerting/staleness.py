from datetime import datetime

from shared.config import (
    STALENESS_THRESHOLD_HOURS_STOCKS,
    STALENESS_THRESHOLD_HOURS_WEATHER,
    ALERT_COOLDOWN_MINUTES,
)
from alerting.callbacks import _get_writer
from alerting.notifier import _send_slack_message
from alerting.cooldown import (
    _should_send_alert,
    _record_alert_sent,
    _clear_alert_state,
    _should_send_staleness_recovery,
)


def check_data_staleness() -> None:
    """
    Query MAX timestamps from both Snowflake MARTS tables and alert if data exceeds
    staleness thresholds. Called by the staleness monitoring DAG.
    """
    from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook  # deferred: avoids DAG parse overhead

    writer = _get_writer()
    writer.log(f"Staleness check started: {datetime.now()}")

    # Use the same Snowflake connection the rest of the pipeline uses
    hook = SnowflakeHook(snowflake_conn_id="snowflake_default")
    conn = hook.get_conn()
    cur = conn.cursor()
    alerts = []

    try:
        # Check company_financials freshness (filed_date is a string like "2025-03-15" in FCT_COMPANY_FINANCIALS)
        cur.execute("SELECT MAX(FILED_DATE) FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS")
        latest_filed = cur.fetchone()[0]
        staleness_key_stocks = "alert_last_sent:staleness:company_financials"  # cooldown key for this table

        if latest_filed:
            latest_dt = datetime.strptime(str(latest_filed), "%Y-%m-%d")
            age_hours = (datetime.now() - latest_dt).total_seconds() / 3600
            writer.log(f"  FCT_COMPANY_FINANCIALS: latest filed_date={latest_filed}, age={age_hours:.1f}h, threshold={STALENESS_THRESHOLD_HOURS_STOCKS}h")

            if age_hours > STALENESS_THRESHOLD_HOURS_STOCKS:
                # Only alert if outside the cooldown window
                if _should_send_alert(staleness_key_stocks, ALERT_COOLDOWN_MINUTES):
                    alerts.append(
                        f":clock1: *Stale Data: FCT_COMPANY_FINANCIALS*\n"
                        f"Latest filing: {latest_filed} ({age_hours:.0f}h ago)\n"
                        f"Threshold: {STALENESS_THRESHOLD_HOURS_STOCKS}h"
                    )
                    _record_alert_sent(staleness_key_stocks)  # suppress repeat alerts within window
                else:
                    writer.log("  [STALENESS ALERT - suppressed, within cooldown] company_financials")
            else:
                # Table is fresh — send a recovery message if we previously alerted on it
                if _should_send_staleness_recovery(staleness_key_stocks):
                    recovery_msg = (
                        f":green_circle: *Staleness Resolved: FCT_COMPANY_FINANCIALS*\n"
                        f"Latest filing: {latest_filed} — now within threshold."
                    )
                    writer.log(f"[STALENESS RECOVERY] {recovery_msg}")
                    _send_slack_message(recovery_msg)
                    _clear_alert_state(staleness_key_stocks)  # reset cooldown after recovery
        else:
            # No rows at all — treat the same as stale, with cooldown
            if _should_send_alert(staleness_key_stocks, ALERT_COOLDOWN_MINUTES):
                alerts.append(":clock1: *No data in FCT_COMPANY_FINANCIALS table*")
                _record_alert_sent(staleness_key_stocks)
            else:
                writer.log("  [STALENESS ALERT - suppressed, within cooldown] company_financials (empty)")

        # Check weather freshness (imported_at is TIMESTAMP_NTZ in FCT_WEATHER_HOURLY, converted from epoch in staging)
        cur.execute("SELECT MAX(IMPORTED_AT) FROM PIPELINE_DB.MARTS.FCT_WEATHER_HOURLY")
        latest_imported = cur.fetchone()[0]
        staleness_key_weather = "alert_last_sent:staleness:weather_hourly"  # cooldown key for this table

        if latest_imported:
            # Snowflake TIMESTAMP_NTZ returns a Python datetime; strip tz if present for naive comparison
            latest_dt = latest_imported.replace(tzinfo=None) if isinstance(latest_imported, datetime) else datetime.fromisoformat(str(latest_imported)[:19])
            age_hours = (datetime.now() - latest_dt).total_seconds() / 3600
            writer.log(f"  FCT_WEATHER_HOURLY: latest imported_at={latest_imported}, age={age_hours:.1f}h, threshold={STALENESS_THRESHOLD_HOURS_WEATHER}h")

            if age_hours > STALENESS_THRESHOLD_HOURS_WEATHER:
                # Only alert if outside the cooldown window
                if _should_send_alert(staleness_key_weather, ALERT_COOLDOWN_MINUTES):
                    alerts.append(
                        f":clock1: *Stale Data: FCT_WEATHER_HOURLY*\n"
                        f"Latest import: {latest_imported} ({age_hours:.1f}h ago)\n"
                        f"Threshold: {STALENESS_THRESHOLD_HOURS_WEATHER}h"
                    )
                    _record_alert_sent(staleness_key_weather)  # suppress repeat alerts within window
                else:
                    writer.log("  [STALENESS ALERT - suppressed, within cooldown] weather_hourly")
            else:
                # Table is fresh — send a recovery message if we previously alerted on it
                if _should_send_staleness_recovery(staleness_key_weather):
                    recovery_msg = (
                        f":green_circle: *Staleness Resolved: FCT_WEATHER_HOURLY*\n"
                        f"Latest import: {latest_imported} — now within threshold."
                    )
                    writer.log(f"[STALENESS RECOVERY] {recovery_msg}")
                    _send_slack_message(recovery_msg)
                    _clear_alert_state(staleness_key_weather)  # reset cooldown after recovery
        else:
            # No rows at all — treat the same as stale, with cooldown
            if _should_send_alert(staleness_key_weather, ALERT_COOLDOWN_MINUTES):
                alerts.append(":clock1: *No data in FCT_WEATHER_HOURLY table*")
                _record_alert_sent(staleness_key_weather)
            else:
                writer.log("  [STALENESS ALERT - suppressed, within cooldown] weather_hourly (empty)")

    except Exception as e:
        writer.log(f"  Database error during staleness check: {e}")
        raise
    finally:
        cur.close()
        conn.close()

    # Send one Slack message per stale table (only those that passed the cooldown gate)
    for alert_msg in alerts:
        writer.log(f"[STALENESS ALERT] {alert_msg}")
        _send_slack_message(alert_msg)

    if not alerts:
        writer.log("  All tables within freshness thresholds — no alerts.")
