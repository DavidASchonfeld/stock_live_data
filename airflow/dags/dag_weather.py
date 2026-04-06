# General Libraries

import os
import json
from typing import Annotated, Any, cast
from datetime import datetime, timedelta

import pendulum

from airflow.sdk import dag, task, XComArg  # Airflow 3.x SDK — replaces airflow.decorators and airflow.models.xcom_arg


import pandas as pd
from sqlalchemy import create_engine, text  # text() required for raw SQL in SQLAlchemy 2.x
from sqlalchemy.exc import SQLAlchemyError


# My Files
from weather_client import fetch_weather_forecast  # renamed from sendRequest_openMeteo
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from db_config import DB_USER, DB_PASSWORD, DB_NAME, DB_HOST  # db_config.py is in .gitignore — never commit secrets
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
    description="Pulling weather info from Meteo Weather API",
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
    the `weather_hourly` table in MariaDB.

    The fixed coordinates are arbitrary — chosen for learning purposes.
    In a real deployment you would parameterize these or pull from a config file.

    #### Pipeline stages:
    extract()  →  transform()  →  load()
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
    def load(inData):
        """
        ### Load Task
        Push transformed rows into MariaDB (table: weather_hourly) via SQLAlchemy.
        Deduplicates on (time, latitude, longitude) before inserting to prevent
        unbounded table growth when the DAG reruns within the same forecast window.
        """
        # Location of Logs
        # writer : OutputTextWriter = OutputTextWriter("/home/ec2-user/myK3Spods_files/myAirflow/dag-mylogs")

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer : OutputTextWriter = OutputTextWriter("/opt/airflow/out")


        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run when the DAG is initialized,
        # which would cause issues.

        # Validate DB secrets at task-execution time (not parse time) — prevents DAG parse failures when secrets aren't yet mounted
        _missing = [k for k in ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"] if not os.getenv(k)]
        if _missing:
            raise RuntimeError(f"Missing Kubernetes secrets: {_missing}. Ensure db-credentials secret is mounted.")

        print(str(inData))
        writer.log(str(inData))  # inData is now a list of row-dicts from transform()

        #TODO: Move this conversion to Pandas Dataframe object
        df = pd.DataFrame(inData)  # list-of-dicts → flat DataFrame ready for SQL

        ## Testing/Learning about Python to SQL (with Pandas)



        try:
            # engine = create_engine("mysql+pymysql://USERNAME:PASSWORD@localhost:3306/mydatabase")
            # If Apache Airflow was not inside Kubernetes pod, since MariaDB is already outside a pod: "localhost:3306"  # Default MariaDB Value (This Command in "Command Line" confirms this: "sudo netstat -tulnp | grep 3306")

            engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")

            with engine.connect() as connection:
                result_one = connection.execute(text("SELECT 1"))  # text() wrapper required by SQLAlchemy 2.x
                print(f"Success! {result_one.scalar()}")

            writer.log("--- Pre-insert DataFrame preview ---")
            writer.log(str(df.head()))
            writer.log(str(df.dtypes))

            # Deduplication: skip rows whose (time, latitude, longitude) already exist in the DB
            # to prevent unbounded table growth when the DAG runs more frequently than data refreshes.
            lat = df["latitude"].iloc[0]
            lon = df["longitude"].iloc[0]
            with engine.connect() as dedup_conn:
                existing_times = pd.read_sql(
                    text("SELECT time FROM weather_hourly WHERE latitude=:lat AND longitude=:lon"),
                    dedup_conn, params={"lat": lat, "lon": lon}
                )["time"].tolist()
            new_rows = df[~df["time"].isin(existing_times)]
            writer.log(f"Dedup: {len(existing_times)} existing, {len(new_rows)} new rows to insert")

            if len(new_rows) == 0:
                writer.log("No new rows to insert — all timestamps already present in weather_hourly")
            else:
                ### THIS LINE PUTS THE STUFF INTO SQL DATABASE, AUTOMATICALLY CONVERTING IT INTO A SQL OBJECT
                new_rows.to_sql("weather_hourly", con=engine, if_exists="append", index=False)
                # index = False means: don't write the Pandas Dataframe's index into the SQL table
                writer.log(f"Loaded {len(new_rows)} new rows into weather_hourly table")

            # Dual-write to Snowflake — soft fail so MariaDB load still succeeds before Snowflake is wired up
            try:
                from snowflake_client import write_df_to_snowflake
                # Mirror the same dedup-filtered rows to Snowflake (overwrite=False = append)
                write_df_to_snowflake(new_rows.copy(), "WEATHER_HOURLY", overwrite=False)
                writer.log(f"Loaded {len(new_rows)} rows into Snowflake WEATHER_HOURLY")
            except Exception as sf_err:
                writer.log(f"Snowflake write skipped (not yet configured): {sf_err}")

        except SQLAlchemyError as e:
            writer.log(f"[ERROR] SQLAlchemy {type(e).__name__}: {e}")  # write to PVC so error is readable without the Airflow UI (UI has 404 encoding bug on run IDs with '+')
            print(f"[ERROR] Connection failed: {e}")
            raise
        except Exception as e:
            writer.log(f"[ERROR] Unexpected {type(e).__name__}: {e}")  # catches non-SQLAlchemy errors (e.g. ValueError, TypeError) that would otherwise only appear in stdout
            raise

    # Airflow automatically converts all task method return values to XComArg objects for cross-task data passing.

    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # Calling the @task functions here defines the execution order for Airflow.
    # Airflow reads these at parse time to build the DAG graph; the functions
    # themselves run later at scheduled execution time.
    raw_data : XComArg = extract()
    records : XComArg = transform(raw_data)
    load(records)

dag = weather_pipeline()  # assign to module-level variable — Airflow best practice for DAG discovery
