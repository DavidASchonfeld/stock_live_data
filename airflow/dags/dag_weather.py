# General Libraries

import os
import json
from typing import Annotated, Any, cast
from datetime import datetime, timedelta

import pendulum
from airflow.sdk import dag, task, XComArg, get_current_context, Variable  # Airflow 3.x SDK — replaces airflow.decorators and airflow.models.xcom_arg
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator  # fires consumer DAG after publish

import pandas as pd


# My Files
from weather_client import fetch_weather_forecast  # renamed from sendRequest_openMeteo
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from dag_utils import check_vacation_mode  # shared guard: skips task if VACATION_MODE Variable is "true"
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts on task failure/retry/recovery


# ── Why Open-Meteo instead of OpenWeatherMap? ────────────────────────────────
# Open-Meteo (api.open-meteo.com) is completely free with no API key required.
# The original version used OpenWeatherMap (archived in _archive/old_openWeatherMap.py),
# but it required a paid plan for hourly data. Open-Meteo provides hourly forecasts
# at no cost and with no rate limits — ideal for learning and practice.
#
# Schedule: hourly (matching Open-Meteo's own forecast refresh rate).
#   Open-Meteo returns 168 rows per call (7 days × 24 hours). Running more frequently
#   than once per hour would fetch identical data and create duplicate rows.
#   The deduplication logic in load() guards against this, but hourly is the correct cadence.
# ─────────────────────────────────────────────────────────────────────────────


@dag(  # type:ignore
    "API_Weather-Pull_Data",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        'on_failure_callback': on_failure_alert,  # Slack + PVC log on task failure
        'on_success_callback': on_success_alert,  # Slack recovery message + clear alert state
        'on_retry_callback': on_retry_alert,  # Slack + PVC log on task retry
    },
    description="Weather pipeline: Open-Meteo → Kafka (consumer DAG writes Snowflake → dbt)",
    schedule=timedelta(hours=1),  # Hourly: Open-Meteo refreshes its forecast data once per hour
    # Use fixed past date instead of pendulum.now() to prevent DAG configuration drift on each parse
    start_date=pendulum.datetime(2025, 6, 8, 0, 0, tz="America/New_York"),
    # Note: start_date has to be in the past if you want it to run today/later
    catchup=False,
    tags=["learning","weather","external api pull"]
)

def weather_pipeline():
    """
    ### Weather Data Pipeline

    Pulls hourly temperature forecasts from Open-Meteo for a fixed lat/lon point
    (latitude=40, longitude=40 — Black Sea coast, Turkey) and loads them into
    Snowflake (RAW schema, table: WEATHER_HOURLY) once per day via batch gate.

    The fixed coordinates are arbitrary — chosen for learning purposes.
    In a real deployment you would parameterize these or pull from a config file.

    #### Pipeline stages:
    extract()  →  transform()  →  publish_to_kafka()  →  trigger weather_consumer_pipeline
    (Snowflake write + dbt run in dag_weather_consumer.py)
    """

    @task()
    def extract():
        """
        ### Extract:
        Pull information from Meteo Website
        """

        # Halt this task (and downstream transform/load) if vacation mode is active
        check_vacation_mode()

        raw_data : dict = fetch_weather_forecast(latitude=40, longitude=40, fahrenheit=True)
        print(raw_data)
        # Validate API response structure
        if not all(key in raw_data for key in ["hourly", "hourly_units"]):
            raise ValueError("API response missing required keys: 'hourly', 'hourly_units'")
        if "temperature_2m" not in raw_data["hourly"]:
            raise ValueError("API response missing 'temperature_2m' in hourly data")
        return raw_data


    # @task(multiple_outputs=True)
    #   Only best used if downstream (tasks after this one) tasks need to use different parts of the outputted dictionary-like object.
    #   Returns a dictioanry-like object, separating top level key-value pairs into different XComArg objects
    #   To access the results, it would be similar to accessing dictionary values. For example: load(stuff, transformed["timestamp"])
    @task()
    # def transform(inData: Annotated[XComArg, dict[str, Any]]):
    def transform(inData):
        # Not adding type hinting since type hinting for
        # XComArg causes issues when importing the data into a Pandas dataframe
        # cast() is a no-op at runtime — it only tells the type-checker that inData
        # is a dict. XComArg deserialization returns a plain Python object, not XComArg,
        # so the cast helps IDEs and mypy understand the actual shape.
        inData = cast(dict[str, Any], inData)
        """
        ### Transform task. aka clean/format the data
        """

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer : OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # Transform the incoming JSON into a SQL table, each with a different row for each time (and therfore smae or different temperature)
        # I will need to add
        # --- a primary key
        # --- time of import
        # --- MAYBE: If the input is only about latitude/longitude, maybe I can add a city name?...
        # ------But maybe a city is larger than 1 latitude/longitude

        # Open-Meteo returns paired arrays under "hourly" — zip them into one row per hour
        df : pd.DataFrame = pd.DataFrame({
            "time"            : inData["hourly"]["time"],
            "temperature_2m"  : inData["hourly"]["temperature_2m"],
            "latitude"        : inData["latitude"],
            "longitude"       : inData["longitude"],
            "elevation"       : inData["elevation"],
            "timezone"        : inData["timezone"],
            "utc_offset_seconds": inData["utc_offset_seconds"],
            "imported_at"     : datetime.now().isoformat(),  # audit column: when this row was loaded
        })

        writer.log("----Transform Preview----")
        writer.log(str(df.head()))
        writer.log(str(df.dtypes))

        # Convert to list-of-dicts so Airflow XCom can serialize it as JSON
        return df.to_dict(orient="records")

    @task()
    def publish_to_kafka(records: list[dict[str, Any]]) -> int:
        """
        ### Publish
        Publish the transformed hourly records to Kafka topic weather.hourly.raw.
        Returns record count. The consumer DAG (dag_weather_consumer.py) handles
        the Snowflake dedup write and dbt run.

        One message per DAG run keyed by run_id for idempotency.
        """
        from kafka import KafkaProducer  # kafka-python, installed via _PIP_ADDITIONAL_REQUIREMENTS

        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")
        context = get_current_context()

        bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")  # kafka.kafka.svc.cluster.local:9092
        producer = KafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        )

        # Single message per run — full list-of-dicts as one JSON payload
        producer.send(
            "weather-hourly-raw",
            key=context["run_id"].encode("utf-8"),  # idempotency key: prevents duplicate processing on retry
            value=records,
        )
        producer.flush()   # block until broker acknowledges receipt
        producer.close()

        writer.log(f"Published {len(records)} records to weather-hourly-raw")
        return len(records)

    # Airflow automatically converts all task method return values to XComArg objects for cross-task data passing.

    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # extract → transform → publish_to_kafka → trigger consumer DAG
    # Snowflake write + dbt are handled in dag_weather_consumer.py
    raw_data  : XComArg = extract()
    records   : XComArg = transform(raw_data)
    publish_task          = publish_to_kafka(records)  # type: ignore[arg-type]

    # Fire consumer DAG after publish; consumer owns Snowflake write + dbt
    trigger_consumer = TriggerDagRunOperator(
        task_id="trigger_consumer",
        trigger_dag_id="weather_consumer_pipeline",
        wait_for_completion=False,  # fire-and-forget — consumer DAG has its own retries
    )
    publish_task >> trigger_consumer

dag = weather_pipeline()  # assign to module-level variable — Airflow best practice for DAG discovery
