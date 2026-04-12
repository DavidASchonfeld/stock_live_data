# General Libraries

import json
from typing import Any
from datetime import timedelta, date

import pendulum
from airflow.sdk import dag, task, XComArg, Variable  # Airflow 3.x SDK
from airflow.providers.standard.operators.bash import BashOperator  # calls dbt CLI in its isolated virtualenv
from airflow.providers.standard.operators.python import ShortCircuitOperator  # skips dbt if no new rows written


# My Files
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts


@dag(  # type:ignore
    "stock_consumer_pipeline",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=20),  # hard ceiling: covers consume + write + dbt_run + dbt_test + anomaly detection
        'on_failure_callback': on_failure_alert,
        'on_success_callback': on_success_alert,
        'on_retry_callback': on_retry_alert,
    },
    description="Stocks consumer: reads Kafka → writes Snowflake COMPANY_FINANCIALS → dbt → anomaly detection",
    schedule=None,  # triggered by TriggerDagRunOperator in dag_stocks.py — not time-based
    start_date=pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York"),
    catchup=False,
    tags=["stocks", "kafka", "consumer", "snowflake", "portfolio"]
)
def stock_consumer_pipeline():
    """
    ### Stock Consumer Pipeline

    Triggered by dag_stocks.py after it publishes to Kafka.
    Reads one batch from the stocks.financials.raw topic, writes to
    Snowflake (COMPANY_FINANCIALS), then runs dbt marts.

    #### Pipeline stages:
    consume_from_kafka()  →  write_to_snowflake()  →  check_new_rows  →  dbt_run  →  dbt_test  →  detect_anomalies()
    """

    @task()
    def consume_from_kafka() -> list[dict[str, Any]]:
        """
        ### Consume
        Read the latest batch from stocks.financials.raw.
        Commits offset immediately after read (before Snowflake write).
        Safe because: (a) daily batch gate prevents duplicate writes within a day,
        and (b) stocks uses if_exists=replace so re-processing is idempotent.
        Polls for up to 30s then exits (DAG run already triggered, message should be present).
        """
        from kafka import KafkaConsumer  # kafka-python, installed via _PIP_ADDITIONAL_REQUIREMENTS

        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")
        consumer = KafkaConsumer(
            "stocks-financials-raw",
            bootstrap_servers=bootstrap,
            group_id="stocks-consumer-group",
            auto_offset_reset="latest",
            enable_auto_commit=False,    # manual commit: we control when to advance the bookmark
            consumer_timeout_ms=30000,   # stop polling after 30s — message should arrive quickly after trigger
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        )

        records: list[dict[str, Any]] = []
        for msg in consumer:
            records.extend(msg.value)   # msg.value is list[dict] (the full batch from publish_to_kafka)
            consumer.commit()           # commit here (before Snowflake write); daily gate + replace strategy prevent duplicates
            writer.log(f"Consumed message offset={msg.offset}, partition={msg.partition}")

        consumer.close()
        writer.log(f"consume_from_kafka: {len(records)} records received from Kafka")
        return records


    @task()
    def write_to_snowflake(records: list[dict[str, Any]]) -> int:
        """
        ### Write
        Push records into Snowflake COMPANY_FINANCIALS with a daily batch gate.
        Uses REPLACE strategy — EDGAR returns all historical data each call.
        Returns number of rows written (0 if gate skips the write).
        """
        import pandas as pd                          # deferred: avoid slow pandas load during DAG parse
        from sqlalchemy.exc import SQLAlchemyError   # deferred: used in except clause below; kept with pandas

        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        if not records:
            writer.log("write_to_snowflake: no records received from Kafka — skipping")
            return 0

        df: pd.DataFrame = pd.DataFrame(records)
        writer.log(f"write_to_snowflake: {len(df)} records to process")

        # ─── Daily Batch Gate: write to Snowflake only once per day (cost optimization) ───
        today_iso = date.today().isoformat()
        try:
            last_write = Variable.get("SF_STOCKS_LAST_WRITE_DATE")
        except KeyError:
            last_write = ""  # Variable doesn't exist yet (first run)

        if last_write == today_iso:
            writer.log(f"Daily batch gate: already wrote today ({today_iso}) — skipping")
            return 0

        writer.log(f"Daily batch gate: last write was {last_write}, today is {today_iso} — proceeding")

        try:
            writer.log("--- Pre-insert DataFrame preview ---")
            writer.log(str(df.head()))
            writer.log(str(df.dtypes))

            from snowflake_client import write_df_to_snowflake
            # if_exists="replace": EDGAR returns ALL historical data each call, replace avoids duplicates
            write_df_to_snowflake(df.copy(), "COMPANY_FINANCIALS")
            writer.log(f"Loaded {len(df)} rows into Snowflake COMPANY_FINANCIALS")

            # Advance gate variable after successful write
            Variable.set("SF_STOCKS_LAST_WRITE_DATE", today_iso)
            writer.log(f"Updated SF_STOCKS_LAST_WRITE_DATE to {today_iso}")
            return len(df)

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

    # dbt_run: builds STAGING views and MARTS tables in Snowflake from the freshly loaded RAW data
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
            "--select tag:stocks "               # only run models tagged 'stocks' — skips weather models
            "--project-dir /opt/airflow/dags/dbt "
            "--no-use-colors"                    # cleaner logs in Airflow UI
        ),
    )

    # dbt_test: checks not_null, unique, accepted_values, and singular tests on stocks models
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
            "--select tag:stocks "
            "--project-dir /opt/airflow/dags/dbt "
            "--no-use-colors"
        ),
    )

    # detect_anomalies: runs IsolationForest on FCT_COMPANY_FINANCIALS via ml-venv subprocess
    @task()
    def detect_anomalies() -> dict:
        """
        ### Detect Anomalies
        Runs anomaly_detector.py under /opt/ml-venv (scikit-learn + mlflow).
        Fits IsolationForest on YoY revenue/net-income % changes, writes results
        to PIPELINE_DB.ANALYTICS.FCT_ANOMALIES, and logs the run to MLflow.
        Returns the JSON summary dict: {n_anomalies, n_total, mlflow_run_id}.
        """
        import subprocess

        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # Run anomaly_detector.py in the ml-venv which has scikit-learn and mlflow installed
        result = subprocess.run(
            [
                "/opt/ml-venv/bin/python",
                "/opt/airflow/dags/anomaly_detector.py",
                "--contamination", "0.05",
                "--n-estimators", "100",
            ],
            capture_output=True,
            text=True,
            timeout=300,    # 5-minute ceiling — model fit + Snowflake write should complete well within this
        )

        # Log full stdout so the Airflow UI shows model output and row counts
        for line in result.stdout.splitlines():
            writer.log(line)

        if result.returncode != 0:
            writer.log(f"[ERROR] anomaly_detector stderr: {result.stderr}")
            raise RuntimeError(f"anomaly_detector.py failed (rc={result.returncode})")

        # Last stdout line is the JSON summary printed by anomaly_detector.__main__
        last_line = result.stdout.strip().splitlines()[-1]
        return json.loads(last_line)

    detect_anomalies_task = detect_anomalies()
    check_new_rows >> dbt_run >> dbt_test >> detect_anomalies_task  # dbt only runs if rows were actually written


dag = stock_consumer_pipeline()
