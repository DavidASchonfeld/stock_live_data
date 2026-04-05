# General Libraries

import json
from typing import Annotated, Any, cast
from datetime import datetime, timedelta

import pendulum

from airflow.decorators import dag, task
from airflow.models.xcom_arg import XComArg


import pandas as pd
from sqlalchemy import create_engine, text  # text() required for raw SQL in SQLAlchemy 2.x
from sqlalchemy.exc import SQLAlchemyError


# My Files
from weather_client import sendRequest_openMeteo  # renamed from api_weather_requests
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from db_config import DB_USER, DB_PASSWORD, DB_NAME, DB_HOST  # db_config.py is in .gitignore — never commit secrets
from dag_utils import check_vacation_mode  # shared guard: skips task if VACATION_MODE Variable is "true"
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts on task failure/retry/recovery


# Validate required environment variables are available (fail fast if Kubernetes secrets not injected)
import os
_required_secrets = ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"]
_missing_secrets = [k for k in _required_secrets if not os.getenv(k)]
if _missing_secrets:
    raise RuntimeError(f"Missing Kubernetes secrets (environment variables): {_missing_secrets}. Ensure db-credentials secret is mounted.")


# ── Why Open-Meteo instead of OpenWeatherMap? ────────────────────────────────
# Open-Meteo (api.open-meteo.com) is completely free with no API key required.
# The original version used OpenWeatherMap (archived in _archive/old_openWeatherMap.py),
# but it required a paid plan for hourly data. Open-Meteo provides hourly forecasts
# at no cost and with no rate limits — ideal for learning and practice.
#
# Schedule: every 2 minutes (vs. daily for stocks) because:
#   1. Open-Meteo has no rate limit, so frequent polling is fine
#   2. Provides a fast feedback loop for testing the Airflow → MariaDB path
#   3. Exercises Airflow's sub-hourly scheduling for learning purposes
#   (Weather itself only updates every hour, so rows will repeat — that's OK for now)
# ─────────────────────────────────────────────────────────────────────────────


# default_args =

@dag(  # type:ignore
    "API_Weather-Pull_Data",
    default_args={
        # Brought these "default_args" section from Airflow tutorial codes
        # [START default_args]
        # These args will get passed on to each operator
        # You can override them on a per-task basis during operator initialization
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        # 'queue': 'bash_queue',
        # 'pool': 'backfill',
        # 'priority_weight': 10,
        # 'end_date': datetime(2016, 1, 1),
        # 'wait_for_downstream': False,
        # 'execution_timeout': timedelta(seconds=300),
        'on_failure_callback': on_failure_alert,  # Slack + PVC log on task failure
        'on_success_callback': on_success_alert,  # Slack recovery message + clear alert state
        'on_retry_callback': on_retry_alert,  # Slack + PVC log on task retry
        # 'sla_miss_callback': yet_another_function, # or list of functions
        # 'on_skipped_callback': another_function, #or list of functions
        # 'trigger_rule': 'all_success'
        # [END default_args]
    },
    description="Pulling weather info from Meteo Weather API",
    schedule=timedelta(minutes=5),  # Short interval for development/demo — increase for production
    # Use fixed past date instead of pendulum.now() to prevent DAG configuration drift on each parse
    start_date=pendulum.datetime(2025, 6, 8, 0, 0, tz="America/New_York"),
    # Note: start_date has to be in the past if you want it to run today/later
    catchup=False,
    tags=["learning","weather","external api pull"]
)

def zero_nameThatAirflowUIsees(): #nameThatAirflowUIsees if I don't specify a name in the @dag section above
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

        dictGotten : dict = sendRequest_openMeteo(inLatitude=40, inLongitude=40, inFarenheit=True)
        print(dictGotten)
        # Validate API response structure
        if not all(key in dictGotten for key in ["hourly", "hourly_units"]):
            raise ValueError("API response missing required keys: 'hourly', 'hourly_units'")
        if "temperature_2m" not in dictGotten["hourly"]:
            raise ValueError("API response missing 'temperature_2m' in hourly data")
        return dictGotten
    
    
    # @task(multiple_outputs=True) 
    #   Only best used if downstream (tasks after this one) tasks need to use different parts of the outputted dictionary-like object.
    #   Returns a dictioanry-like object, separating top level key-value pairs into different XComArg objects
    #   To access the results, it would be similar to accessing dictionary values. For example: load(stuff, transformed["timestamp"])
    @task
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
        newDataFrame : pd.DataFrame = pd.DataFrame({
            "time"            : inData["hourly"]["time"],
            "temperature_2m"  : inData["hourly"]["temperature_2m"],
            "latitude"        : inData["latitude"],
            "longitude"       : inData["longitude"],
            "elevation"       : inData["elevation"],
            "timezone"        : inData["timezone"],
            "utc_offset_seconds": inData["utc_offset_seconds"],
            "imported_at"     : datetime.now().isoformat(),  # audit column: when this row was loaded
        })

        writer.print("----Transform Preview----")
        writer.print(str(newDataFrame.head()))
        writer.print(str(newDataFrame.dtypes))

        # Convert to list-of-dicts so Airflow XCom can serialize it as JSON
        return newDataFrame.to_dict(orient="records")
    @task()
    def load(inData):
        """
        ### Load Task
        Push to the storage
        (In this case, push to Kafka topic,
        and a different script will take
        the data from the kafka topic
        and put it into the SQL database))
        """
        # Location of Logs
        # writer : OutputTextWriter = OutputTextWriter("/home/ec2-user/myK3Spods_files/myAirflow/dag-mylogs")

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push 
        writer : OutputTextWriter = OutputTextWriter("/opt/airflow/out")


        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run whe nthe DAG is initalized,
        # which would cause issues.

        print(str(inData))
        writer.print(str(inData))  # inData is now a list of row-dicts from transform()

        #TODO: Move this conversion to Pandas Dataframe object
        # myDataFrameThing = pd.DataFrame([inData])
        myDataFrameThing = pd.DataFrame(inData)  # list-of-dicts → flat DataFrame ready for SQL

        ## Testing/Learning about Python to SQL (with Pandas)



        try:
            # engine = create_engine("mysql+pymysql://USERNAME:PASSWORD@localhost:3306/mydatabase")
            # If Apache Airflow was not inside Kubernetes pod, since MariaDB is already outside a pod: "localhost:3306"  # Default MariaDB Value (This Command in "Command Line" confirms this: "sudo netstat -tulnp | grep 3306")

            engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")

            with engine.connect() as connection:
                result_one = connection.execute(text("SELECT 1"))  # text() wrapper required by SQLAlchemy 2.x
                print("Success! "+str(result_one.scalar()))

            writer.print("----AAA----")
            writer.print(str(myDataFrameThing.head()))
            writer.print(str(myDataFrameThing.dtypes))
            writer.print("----BBB----")

            ### THIS LINE PUTS THE STUFF INTO SQL DATABASE, AUTOAMTICALLY CONVERTING IT INTO A SQL OBJECT
            myDataFrameThing.to_sql("weather_hourly", con=engine, if_exists="append", index=False)

            # index = False means: don't write the Pandas Dataframe's index into the SQL table
            writer.print(f"Loaded {len(myDataFrameThing)} rows into weather_hourly table")  # confirm row count written

            # Dual-write to Snowflake — soft fail so MariaDB load still succeeds before Snowflake is wired up
            try:
                from snowflake_client import write_df_to_snowflake
                write_df_to_snowflake(myDataFrameThing.copy(), "WEATHER_HOURLY")
                writer.print(f"Loaded {len(myDataFrameThing)} rows into Snowflake WEATHER_HOURLY")
            except Exception as sf_err:
                writer.print(f"Snowflake write skipped (not yet configured): {sf_err}")

        except SQLAlchemyError as e:
            print("Connection failed. Error: "+str(e))
            raise

        # def flattenDictIntoJSON(inCell):
        #     return "horshoe"
        # myDataFrameThing : pd.DataFrame = myDataFrameThing.applymap(flattenDictIntoJSON)


        # Still have to install MySQL etc. the d
        # Would need "pip install pymysql"
        # Also, URL for SQL might be different since this script will be in kuberentes pod
        # and my SQL db will be outside the Kubernetes pod



    
    # Airflow auomatically converts all tsak method outputs to XComArg objects.
    # If I want the objects treated as the types I want

    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # Calling the @task functions here defines the execution order for Airflow.
    # Airflow reads these at parse time to build the DAG graph; the functions
    # themselves run later at scheduled execution time.
    order_data : XComArg = extract()
    order_summary : XComArg = transform(order_data)
    load(order_summary)
zero_nameThatAirflowUIsees()