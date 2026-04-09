# General Libraries

import json
from typing import Any
from datetime import timedelta, date

import pendulum
from airflow.sdk import dag, task, XComArg, Variable  # Airflow 3.x SDK
from airflow.providers.standard.operators.bash import BashOperator  # calls dbt CLI in its isolated virtualenv
from airflow.providers.standard.operators.python import ShortCircuitOperator  # skips dbt if no new rows written

import pandas as pd
from sqlalchemy.exc import SQLAlchemyError


# My Files
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts


@dag(  # type:ignore
    "weather_consumer_pipeline",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        'on_failure_callback': on_failure_alert,
        'on_success_callback': on_success_alert,
        'on_retry_callback': on_retry_alert,
    },
    description="Weather consumer: reads Kafka → dedup-writes Snowflake WEATHER_HOURLY → dbt",
    schedule=None,  # triggered by TriggerDagRunOperator in dag_weather.py — not time-based
    start_date=pendulum.datetime(2025, 6, 8, 0, 0, tz="America/New_York"),
    catchup=False,
    tags=["weather", "kafka", "consumer", "snowflake", "learning"]
)
def weather_consumer_pipeline():
    """
    ### Weather Consumer Pipeline

    Triggered by dag_weather.py after it publishes to Kafka.
    Reads one batch from the weather.hourly.raw topic, deduplicates
    against existing Snowflake timestamps, appends new rows to
    WEATHER_HOURLY, then runs dbt marts.

    #### Pipeline stages:
    consume_from_kafka()  →  write_to_snowflake()  →  check_new_rows  →  dbt_run  →  dbt_test
    """

    @task()
    def consume_from_kafka() -> list[dict[str, Any]]:
        """
        ### Consume
        Read the latest batch from weather.hourly.raw.
        Commits offset only after successful read — prevents message loss on retry.
        Polls for up to 30s then exits (DAG run already triggered, message should be present).
        """
        from kafka import KafkaConsumer  # kafka-python, installed via _PIP_ADDITIONAL_REQUIREMENTS

        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")
        consumer = KafkaConsumer(
            "weather-hourly-raw",
            bootstrap_servers=bootstrap,
            group_id="weather-consumer-group",
            auto_offset_reset="latest",
            enable_auto_commit=False,    # manual commit: offset advances only after Snowflake write
            consumer_timeout_ms=30000,   # stop polling after 30s — message should arrive quickly after trigger
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )

        records: list[dict[str, Any]] = []
        for msg in consumer:
            records.extend(msg.value)   # msg.value is list[dict] (the full batch from publish_to_kafka)
            consumer.commit()           # advance offset after reading
            writer.log(f"Consumed message offset={msg.offset}, partition={msg.partition}")

        consumer.close()
        writer.log(f"consume_from_kafka: {len(records)} records received from Kafka")
        return records


    @task()
    def write_to_snowflake(records: list[dict[str, Any]]) -> int:
        """
        ### Write
        Dedup-append records into Snowflake WEATHER_HOURLY with a daily batch gate.
        Deduplicates against existing timestamps — Open-Meteo returns 168 rows per call
        (7-day forecast window) so re-runs would insert duplicates without this check.
        Returns number of net-new rows written (0 if gate or dedup skips the write).
        """
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        if not records:
            writer.log("write_to_snowflake: no records received from Kafka — skipping")
            return 0

        df: pd.DataFrame = pd.DataFrame(records)
        writer.log(f"write_to_snowflake: {len(df)} records to process")

        writer.log("--- Pre-insert DataFrame preview ---")
        writer.log(str(df.head()))
        writer.log(str(df.dtypes))

        # ─── Daily Batch Gate: write to Snowflake only once per day (cost optimization) ───
        today_iso = date.today().isoformat()
        try:
            last_write = Variable.get("SF_WEATHER_LAST_WRITE_DATE")
        except KeyError:
            last_write = ""  # Variable doesn't exist yet (first run)

        if last_write == today_iso:
            writer.log(f"Daily batch gate: already wrote today ({today_iso}) — skipping")
            return 0

        writer.log(f"Daily batch gate: last write was {last_write}, today is {today_iso} — proceeding")

        try:
            from snowflake_client import write_df_to_snowflake
            from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

            # Dedup against existing Snowflake timestamps before inserting
            sf_hook = SnowflakeHook(snowflake_conn_id="snowflake_default")
            sf_conn = sf_hook.get_conn()
            sf_cur = sf_conn.cursor()
            try:
                sf_cur.execute("SELECT TIME FROM PIPELINE_DB.RAW.WEATHER_HOURLY")
                # TIME column is NUMBER(38,0) storing epoch seconds; convert to int for comparison
                sf_existing = {int(row[0]) for row in sf_cur.fetchall()}
                writer.log(f"Snowflake has {len(sf_existing)} existing timestamps")
            except Exception as query_err:
                writer.log(f"Snowflake table doesn't exist yet or query failed: {query_err}")
                sf_existing = set()  # table doesn't exist yet
            sf_cur.close()
            sf_conn.close()

            # Convert df["time"] ISO strings to epoch seconds for comparison with Snowflake NUMBER column
            df_times_epoch = pd.to_datetime(df["time"]).astype(int) // 10**9  # ns to seconds
            sf_new_rows = df[~df_times_epoch.isin(sf_existing)].copy()
            writer.log(f"Snowflake dedup: {len(sf_existing)} existing, {len(sf_new_rows)} new rows")

            if len(sf_new_rows) > 0:
                # Cast ALL columns to match Snowflake table schema exactly
                sf_new_rows["time"] = pd.to_datetime(sf_new_rows["time"])
                sf_new_rows["imported_at"] = pd.to_datetime(sf_new_rows["imported_at"])
                sf_new_rows["temperature_2m"] = sf_new_rows["temperature_2m"].astype(float)
                sf_new_rows["latitude"] = sf_new_rows["latitude"].astype(float)
                sf_new_rows["longitude"] = sf_new_rows["longitude"].astype(float)
                sf_new_rows["elevation"] = sf_new_rows["elevation"].astype(float)
                sf_new_rows["timezone"] = sf_new_rows["timezone"].astype(str)
                sf_new_rows["utc_offset_seconds"] = sf_new_rows["utc_offset_seconds"].astype("int64")
                write_df_to_snowflake(sf_new_rows, "WEATHER_HOURLY", overwrite=False)
                writer.log(f"Loaded {len(sf_new_rows)} rows into Snowflake WEATHER_HOURLY")
            else:
                writer.log("No new rows to insert — all timestamps already present in Snowflake")

            # Advance gate variable even if no new rows (prevents retry writes within the same day)
            Variable.set("SF_WEATHER_LAST_WRITE_DATE", today_iso)
            writer.log(f"Updated SF_WEATHER_LAST_WRITE_DATE to {today_iso}")
            return len(sf_new_rows)

        except SQLAlchemyError as e:
            writer.log(f"[ERROR] SQLAlchemy {type(e).__name__}: {e}")
            raise
        except Exception as e:
            writer.log(f"[ERROR] Unexpected {type(e).__name__}: {e}")
            raise


    def _has_new_rows(row_count: int) -> bool:
        """Return True only if rows were actually written — gates dbt to avoid unnecessary runs."""
        return row_count > 0


    # ── Wiring the pipeline ───────────────────────────────────────────────────
    records   : XComArg = consume_from_kafka()
    row_count : XComArg = write_to_snowflake(records)   # type: ignore[arg-type]

    # ShortCircuitOperator defined after row_count so op_args can reference the XComArg directly.
    # Passing row_count as op_args both supplies the value AND infers the upstream dependency.
    check_new_rows = ShortCircuitOperator(
        task_id="check_new_rows",
        python_callable=_has_new_rows,
        op_args=[row_count],  # XComArg resolved at runtime — skips dbt if 0 rows written
    )

    # dbt_run: builds STAGING views and MARTS tables in Snowflake from the freshly appended RAW data
    dbt_run = BashOperator(
        task_id="dbt_run",
        bash_command=(
            "mkdir -p /tmp/dbt_target /tmp/dbt_logs && "  # ensure artifact dirs exist before dbt-ol runs
            "PATH=/opt/dbt-venv/bin:$PATH "      # ensures dbt-ol's internal subprocess.Popen(['dbt']) resolves correctly
            "DBT_PROFILES_DIR=/dbt "
            "OPENLINEAGE_CONFIG=/opt/openlineage.yml "  # emits lineage events via console transport
            "DBT_TARGET_PATH=/tmp/dbt_target "   # dbt-ol uses this env var for both artifact writing and post-run reading
            "DBT_LOG_PATH=/tmp/dbt_logs "        # dbt 1.8+: replaces deprecated log-path in dbt_project.yml
            "/opt/dbt-venv/bin/dbt-ol run "      # dbt-ol wraps dbt run and emits OpenLineage events after completion
            "--select tag:weather "              # only run models tagged 'weather' — skips stocks models
            "--project-dir /opt/airflow/dags/dbt "
            "--no-use-colors"                    # cleaner logs in Airflow UI
        ),
    )

    # dbt_test: checks not_null, unique, and accepted_values on weather models
    dbt_test = BashOperator(
        task_id="dbt_test",
        bash_command=(
            "mkdir -p /tmp/dbt_target /tmp/dbt_logs && "  # ensure artifact dirs exist before dbt-ol runs
            "PATH=/opt/dbt-venv/bin:$PATH "      # ensures dbt-ol's internal subprocess.Popen(['dbt']) resolves correctly
            "DBT_PROFILES_DIR=/dbt "
            "OPENLINEAGE_CONFIG=/opt/openlineage.yml "  # emits lineage events via console transport
            "DBT_TARGET_PATH=/tmp/dbt_target "   # dbt-ol uses this env var for artifact reading and writing
            "DBT_LOG_PATH=/tmp/dbt_logs "        # dbt 1.8+: write log file to /tmp, not project-dir
            "/opt/dbt-venv/bin/dbt-ol test "     # dbt-ol wraps dbt test and emits events after completion
            "--select tag:weather "
            "--project-dir /opt/airflow/dags/dbt "
            "--no-use-colors"
        ),
    )

    check_new_rows >> dbt_run >> dbt_test  # dbt only runs if rows were actually written


dag = weather_consumer_pipeline()
