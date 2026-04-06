# General Libraries

import json
import time
from typing import Any
from datetime import timedelta

import pendulum

from airflow.decorators import dag, task
from airflow.models.xcom_arg import XComArg

import pandas as pd
from sqlalchemy import create_engine, text  # text() required for raw SQL in SQLAlchemy 2.x
from sqlalchemy.exc import SQLAlchemyError


# My Files
from stock_client import resolve_cik, fetch_company_facts, flatten_company_financials  # re-exported from edgar_client.py
from file_logger import OutputTextWriter  # renamed from outputTextWriter
from db_config import DB_USER, DB_PASSWORD, DB_NAME, DB_HOST  # db_config.py is in .gitignore — never commit secrets
from dag_utils import check_vacation_mode  # shared guard: skips task if VACATION_MODE Variable is "true"
from alerting import on_failure_alert, on_retry_alert, on_success_alert  # Slack + PVC log alerts on task failure/retry/recovery


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
# 3 tickers × 2 API calls each (CIK lookup + companyfacts) = 6 calls total
# SEC EDGAR allows 10 requests/second with no daily limit — no quota concern
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
        'on_failure_callback': on_failure_alert,  # Slack + PVC log on task failure
        'on_success_callback': on_success_alert,  # Slack recovery message + clear alert state
        'on_retry_callback': on_retry_alert,  # Slack + PVC log on task retry
        # 'sla_miss_callback': yet_another_function, # or list of functions
        # 'on_skipped_callback': another_function, #or list of functions
        # 'trigger_rule': 'all_success'
        # [END default_args]
    },
    description="Company financials pipeline: SEC EDGAR XBRL → MariaDB (→ Snowflake in Step 2)",
    schedule=timedelta(minutes=5),  # Short interval for development/demo — increase for production
    # start_date must be in the past for Airflow to schedule the first run immediately
    # Use fixed past date instead of pendulum.now() to prevent DAG configuration drift on each parse
    start_date=pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York"),
    # catchup=False: without this, Airflow would try to run one instance per week
    # starting from start_date until today, creating many queued runs on first deploy.
    # We skip that because SEC EDGAR companyfacts already returns all historical data
    # in the very first successful run.
    catchup=False,  # don't backfill historical runs when DAG is first deployed
    tags=["stocks", "sec_edgar", "financials", "mariadb", "portfolio"]
)
def stock_market_pipeline():
    """
    ### Company Financials Data Pipeline

    Pulls financial data (revenue, net income, EPS, assets, etc.) for a list
    of tickers from SEC EDGAR's XBRL API, flattens the nested XBRL JSON into
    a tabular format, and loads it into MariaDB.

    Data source: SEC EDGAR (U.S. government, public domain, no API key needed)

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
        Fetch raw XBRL financial data for each ticker from SEC EDGAR.
        Returns a list of raw API responses (one dict per ticker).
        """

        # Halt this task (and downstream transform/load) if vacation mode is active
        check_vacation_mode()

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run when the DAG is initialized,
        # which would cause issues.

        results: list[dict[str, Any]] = []

        for ticker in TICKERS:
            writer.print(f"Resolving CIK for: {ticker}")
            # SEC EDGAR uses CIK numbers, not ticker symbols — resolve_cik() handles the mapping
            cik = resolve_cik(ticker)
            writer.print(f"  CIK: {cik}")

            writer.print(f"Fetching company facts for: {ticker}")
            # fetch_company_facts() calls SEC EDGAR's XBRL API with built-in rate limiting
            raw_response = fetch_company_facts(cik)

            # Validate response structure before storing (fail fast on API failures)
            if not raw_response or "facts" not in raw_response:
                raise ValueError(f"Invalid API response for {ticker} (CIK {cik}): missing 'facts' key")
            if "us-gaap" not in raw_response.get("facts", {}):
                raise ValueError(f"No US-GAAP data for {ticker} (CIK {cik}) — company may use IFRS")

            # Store ticker alongside its raw response so transform() knows which symbol it belongs to
            results.append({"ticker": ticker, "cik": cik, "raw": raw_response})
            # Count how many US-GAAP concepts were returned for logging visibility
            gaap_count = len(raw_response["facts"]["us-gaap"])
            writer.print(f"  ✓ {ticker}: {gaap_count} US-GAAP concepts received")

        return results


    @task()
    def transform(raw_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        ### Transform
        Flatten each ticker's nested XBRL JSON into a list of row-dicts.
        One row per ticker per financial metric per reporting period.

        Input shape  (from extract):  [{ "ticker": "AAPL", "cik": "...", "raw": { ... } }, ...]
        Output shape (to load):       [{ "ticker", "cik", "entity_name", "metric", "label",
                                         "period_end", "value", "filed_date", "form_type",
                                         "fiscal_year", "fiscal_period", "frame" }, ...]
        """

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        all_records: list[dict[str, Any]] = []

        for item in raw_data:
            ticker = item["ticker"]
            # flatten_company_financials() lives in edgar_client.py — keeps transform() clean
            rows = flatten_company_financials(ticker, item["raw"], annual_only=True)
            all_records.extend(rows)
            writer.print(f"  {ticker}: {len(rows)} rows after flatten (10-K annual filings only)")

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
        Push transformed rows into MariaDB (table: company_financials).

        Uses REPLACE strategy: drops and recreates the table on each run because
        SEC EDGAR companyfacts returns ALL historical data in every response.
        This avoids duplicate rows without needing a primary key or upsert logic.

        #### TODO (Step 2 of career plan):
        Swap MariaDB for Snowflake:
            from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook
            from snowflake.connector.pandas_tools import write_pandas
            hook = SnowflakeHook(snowflake_conn_id="snowflake_default")
            conn = hook.get_conn()
            write_pandas(conn, df, "RAW_COMPANY_FINANCIALS", auto_create_table=True)
        """
        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run when the DAG is initialized,
        # which would cause issues.

        # Validate DB secrets at task-execution time (not parse time) — prevents DAG parse failures when secrets aren't yet mounted
        import os
        _missing = [k for k in ["DB_USER", "DB_PASSWORD", "DB_HOST", "DB_NAME"] if not os.getenv(k)]
        if _missing:
            raise RuntimeError(f"Missing Kubernetes secrets: {_missing}. Ensure db-credentials secret is mounted.")

        print(str(records[:2]))  # log first 2 rows so Airflow task log shows data arrived
        writer.print(str(records[:2]))

        # list-of-dicts → flat DataFrame ready for SQL
        df: pd.DataFrame = pd.DataFrame(records)

        try:
            # Why mysql+pymysql://? SQLAlchemy needs a driver prefix; pymysql is a
            # pure-Python MySQL/MariaDB driver that requires no C extensions to install.
            # DB_HOST points to MariaDB's private EC2 IP — reachable from inside the K8s
            # cluster because MariaDB runs on the same EC2 host (outside the pods).
            engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")

            with engine.connect() as connection:
                result_one = connection.execute(text("SELECT 1"))  # text() wrapper required by SQLAlchemy 2.x
                print("Success! "+str(result_one.scalar()))

            writer.print("----AAA----")
            writer.print(str(df.head()))
            writer.print(str(df.dtypes))
            writer.print("----BBB----")

            ### THIS LINE PUTS THE STUFF INTO SQL DATABASE, AUTOAMTICALLY CONVERTING IT INTO A SQL OBJECT
            # if_exists="replace": SEC EDGAR returns ALL historical data each call, so we
            # replace the entire table to avoid duplicates. Unlike Alpha Vantage (which
            # returned only recent data and needed "append"), EDGAR gives us everything.
            df.to_sql("company_financials", con=engine, if_exists="replace", index=False)

            # index = False means: don't write the Pandas Dataframe's index into the SQL table
            writer.print(f"Loaded {len(df)} rows into company_financials table")  # confirm row count written

            # Dual-write to Snowflake — soft fail so MariaDB load still succeeds before Snowflake is wired up
            try:
                from snowflake_client import write_df_to_snowflake
                write_df_to_snowflake(df.copy(), "COMPANY_FINANCIALS")
                writer.print(f"Loaded {len(df)} rows into Snowflake COMPANY_FINANCIALS")
            except Exception as sf_err:
                writer.print(f"Snowflake write skipped (not yet configured): {sf_err}")

        except SQLAlchemyError as e:
            # Re-raise so task fails and Airflow can retry (instead of silent failure)
            writer.print(f"[ERROR] SQLAlchemy {type(e).__name__}: {e}")
            raise
        except Exception as e:
            writer.print(f"[ERROR] Unexpected {type(e).__name__}: {e}")  # catches non-SQLAlchemy errors so they appear in PVC log, not just stdout
            raise


    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # Calling the @task functions here (inside the @dag function body) is what
    # tells Airflow about the dependency order: extract → transform → load.
    # Airflow reads these calls at DAG-parse time to build the task graph; the
    # actual Python code inside each function runs later at execution time.
    raw_data:    XComArg = extract()
    transformed: XComArg = transform(raw_data)   # type: ignore[arg-type]
    load(transformed)                             # type: ignore[arg-type]


dag = stock_market_pipeline()
