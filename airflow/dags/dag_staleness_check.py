# General Libraries

from datetime import timedelta

import pendulum

from airflow.sdk import dag, task  # Airflow 3.x SDK — replaces airflow.decorators


# My Files
from dag_utils import check_vacation_mode  # shared guard: skips task if VACATION_MODE Variable is "true"
from alerting import on_failure_alert, on_retry_alert  # Slack + PVC log alerts


@dag(  # type:ignore
    "Data_Staleness_Monitor",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        'on_failure_callback': on_failure_alert,  # alert if the monitor itself breaks
        'on_retry_callback': on_retry_alert,
    },
    description="Monitors data freshness in company_financials and weather_hourly tables",
    schedule=None,  # Manual trigger only to minimize Snowflake query costs (cost optimization)
    is_paused_upon_creation=True,  # Stays paused after deploy — must be manually unpaused to run
    start_date=pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York"),
    catchup=False,
    tags=["monitoring", "alerting", "staleness"]
)
def data_staleness_monitor():
    """
    ### Data Staleness Monitor

    Checks how old the latest data is in each table and sends a Slack alert
    (or logs) if any table exceeds its freshness threshold.

    **Manual trigger only** — run via Airflow UI or API to avoid repeated Snowflake queries.

    Respects vacation mode — stale data is expected when pipelines are paused.
    """

    @task()
    def run_staleness_check() -> None:
        """Query MAX timestamps and alert if data is stale."""
        from alerting.staleness import check_data_staleness  # deferred: avoids loading sqlalchemy during DAG parse (reduces dagProcessor memory)
        # Skip during vacation — stale data is expected when pipelines are paused
        check_vacation_mode()
        check_data_staleness()

    run_staleness_check()


dag = data_staleness_monitor()
