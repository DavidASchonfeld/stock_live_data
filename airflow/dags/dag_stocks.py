# General Libraries

import json
from typing import Any
from datetime import timedelta

import pendulum

from airflow.decorators import dag, task
from airflow.models.xcom_arg import XComArg

import pandas as pd
from sqlalchemy import create_engine, text  # text() required for raw SQL in SQLAlchemy 2.x
from sqlalchemy.exc import SQLAlchemyError


# My Files
from stock_client import sendRequest_alphavantage_daily, flatten_daily_timeseries  # renamed from api_stock_requests
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from api_key import api_keys  # api_key.py is in .gitignore — never commit secrets
from db_config import DB_USER, DB_PASSWORD, DB_NAME, DB_HOST  # db_config.py is in .gitignore — never commit secrets


# ── Why TaskFlow API (@dag / @task) instead of classic Airflow Operators? ─────
# TaskFlow lets you write tasks as plain Python functions and pass data between
# them using return values. Under the hood Airflow serializes the return value
# as JSON, stores it in its metadata DB (this is called XCom — cross-task
# communication), and deserializes it when the next task runs.
#
# Because XCom uses JSON, only JSON-safe types can travel between tasks:
# dict, list, str, int, float, bool. That's why transform() returns list[dict]
# instead of a Pandas DataFrame — DataFrames are not JSON-serializable.
#
# Classic Operators are better when you need tight control over retries,
# sensors, or non-Python work (SQL, Bash). For a pure-Python ETL like this,
# TaskFlow is simpler and more readable.
# ─────────────────────────────────────────────────────────────────────────────


# ── Tickers to track ─────────────────────────────────────────────────────────
# 3 tickers × 1 call each = 3 of our 25 free Alpha Vantage calls per day
TICKERS: list[str] = ["AAPL", "MSFT", "GOOGL"]
# ─────────────────────────────────────────────────────────────────────────────


@dag(  # type:ignore
    "Stock_Market_Pipeline",
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
        # 'on_failure_callback': some_function, # or list of functions
        # 'on_success_callback': some_other_function, # or list of functions
        # 'on_retry_callback': another_function, # or list of functions
        # 'sla_miss_callback': yet_another_function, # or list of functions
        # 'on_skipped_callback': another_function, #or list of functions
        # 'trigger_rule': 'all_success'
        # [END default_args]
    },
    description="Daily stock market pipeline: Alpha Vantage → MariaDB (→ Snowflake in Step 2)",
    schedule=timedelta(days=1),  # run once per day — matches market data cadence
    # start_date must be in the past for Airflow to schedule the first run immediately
    start_date=pendulum.now("America/New_York").subtract(days=1),
    # catchup=False: without this, Airflow would try to run one instance per day
    # starting from start_date until today, creating dozens of queued runs on
    # first deploy. We skip that because Alpha Vantage "compact" already returns
    # the last 100 days in the very first successful run.
    catchup=False,  # don't backfill historical runs when DAG is first deployed
    tags=["stocks", "alpha_vantage", "mariadb", "portfolio"]
)
def stock_market_pipeline():
    """
    ### Stock Market Data Pipeline

    Pulls daily OHLCV (Open, High, Low, Close, Volume) data for a list of
    tickers from Alpha Vantage, flattens the nested JSON into a tabular
    format, and loads it into MariaDB.

    #### Pipeline stages:
    extract()  →  transform()  →  load()

    #### TODO (Step 2 of career plan):
    Replace the MariaDB load with a Snowflake load:
        - Install: apache-airflow-providers-snowflake, snowflake-connector-python
        - Add a Snowflake Connection in the Airflow UI
        - Use SnowflakeHook + write_pandas() instead of SQLAlchemy to_sql()
    """

    @task()
    def extract() -> list[dict[str, Any]]:
        """
        ### Extract
        Fetch raw daily OHLCV data for each ticker from Alpha Vantage.
        Returns a list of raw API responses (one dict per ticker).
        """

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run when the DAG is initialized,
        # which would cause issues.

        # Guard: with 1 retry per ticker, max safe tickers = floor(25 / 2) = 12
        assert len(TICKERS) <= 12, (
            f"Too many tickers ({len(TICKERS)}). "
            "Alpha Vantage free tier allows 25 calls/day; "
            "with 1 retry per ticker the safe ceiling is 12 tickers."
        )

        results: list[dict[str, Any]] = []

        for ticker in TICKERS:
            writer.print(f"Fetching: {ticker}")
            raw_response = sendRequest_alphavantage_daily(
                symbol=ticker,
                api_key=api_keys.alpha_vantage["key"],
                outputsize="compact",  # last 100 trading days — saves API quota
            )
            # Store ticker alongside its raw response so transform() knows which symbol it belongs to
            results.append({"ticker": ticker, "raw": raw_response})
            writer.print(f"  ✓ {ticker}: {len(raw_response.get('Time Series (Daily)', {}))} days received")

        return results


    @task()
    def transform(raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        ### Transform
        Flatten each ticker's nested JSON time-series into a list of row-dicts.
        One row per ticker per trading day.

        Input shape  (from extract):  [{ "ticker": "AAPL", "raw": { ... } }, ...]
        Output shape (to load):       [{ "ticker", "date", "open", "high", "low", "close", "volume" }, ...]
        """

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        all_records: list[dict[str, Any]] = []

        for item in raw_data:
            ticker = item["ticker"]
            # flatten_daily_timeseries() lives in api_stock_requests.py — keeps transform() clean
            rows = flatten_daily_timeseries(ticker, item["raw"])
            all_records.extend(rows)
            writer.print(f"  {ticker}: {len(rows)} rows after flatten")

        # Preview the transformed data
        preview_df: pd.DataFrame = pd.DataFrame(all_records)
        writer.print("----Transform Preview----")
        writer.print(str(preview_df.head()))
        writer.print(str(preview_df.dtypes))

        # Convert to list-of-dicts so Airflow XCom can serialize it as JSON
        return all_records


    @task()
    def load(records: list[dict[str, Any]]) -> None:
        """
        ### Load
        Push transformed rows into MariaDB (table: stock_daily_prices).

        #### TODO (Step 2 of career plan):
        Swap MariaDB for Snowflake:
            from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
            from snowflake.connector.pandas_tools import write_pandas
            hook = SnowflakeHook(snowflake_conn_id="snowflake_default")
            conn = hook.get_conn()
            write_pandas(conn, df, "RAW_STOCK_DAILY_PRICES", auto_create_table=True)
        """
        # Location of Logs
        # writer : OutputTextWriter = OutputTextWriter("/home/ec2-user/myK3Spods_files/myAirflow/dag-mylogs")

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run when the DAG is initialized,
        # which would cause issues.

        print(str(records[:2]))  # log first 2 rows so Airflow task log shows data arrived
        writer.print(str(records[:2]))

        # list-of-dicts → flat DataFrame ready for SQL
        myDataFrameThing: pd.DataFrame = pd.DataFrame(records)

        ## Testing/Learning about Python to SQL (with Pandas)

        try:
            # engine = create_engine("mysql+pymysql://USERNAME:PASSWORD@localhost:3306/mydatabase")
            # If Apache Airflow was not inside Kubernetes pod, since MariaDB is already outside a pod: "localhost:3306"  # Default MariaDB Value (This Command in "Command Line" confirms this: "sudo netstat -tulnp | grep 3306")

            # Why mysql+pymysql://? SQLAlchemy needs a driver prefix; pymysql is a
            # pure-Python MySQL/MariaDB driver that requires no C extensions to install.
            # DB_HOST points to MariaDB's private EC2 IP — reachable from inside the K8s
            # cluster because MariaDB runs on the same EC2 host (outside the pods).
            engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")

            with engine.connect() as connection:
                result_one = connection.execute(text("SELECT 1"))  # text() wrapper required by SQLAlchemy 2.x
                print("Success! "+str(result_one.scalar()))

            writer.print("----AAA----")
            writer.print(str(myDataFrameThing.head()))
            writer.print(str(myDataFrameThing.dtypes))
            writer.print("----BBB----")

            ### THIS LINE PUTS THE STUFF INTO SQL DATABASE, AUTOAMTICALLY CONVERTING IT INTO A SQL OBJECT
            # if_exists="append": each daily run adds new rows; the table accumulates history over time.
            # Alternative "replace" would wipe the table on every run — we want to keep history.
            myDataFrameThing.to_sql("stock_daily_prices", con=engine, if_exists="append", index=False)

            # index = False means: don't write the Pandas Dataframe's index into the SQL table
            writer.print(f"Loaded {len(myDataFrameThing)} rows into stock_daily_prices table")  # confirm row count written

        except SQLAlchemyError as e:
            print("Connection failed. Error: "+str(e))

        # Still have to install MySQL etc. the d
        # Would need "pip install pymysql"
        # Also, URL for SQL might be different since this script will be in kuberentes pod
        # and my SQL db will be outside the Kubernetes pod


    # Airflow automatically converts all task method outputs to XComArg objects.
    # If I want the objects treated as the types I want

    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # Calling the @task functions here (inside the @dag function body) is what
    # tells Airflow about the dependency order: extract → transform → load.
    # Airflow reads these calls at DAG-parse time to build the task graph; the
    # actual Python code inside each function runs later at execution time.
    raw_data:    XComArg = extract()
    transformed: XComArg = transform(raw_data)   # type: ignore[arg-type]
    load(transformed)                             # type: ignore[arg-type]


dag = stock_market_pipeline()
