# General Libraries

import os
import json
from typing import Any
from datetime import timedelta

import pendulum
from airflow.sdk import dag, task, XComArg, get_current_context, Variable  # Airflow 3.x SDK — replaces airflow.decorators and airflow.models.xcom_arg
from airflow.providers.standard.operators.trigger_dagrun import TriggerDagRunOperator  # fires consumer DAG after publish

# My Files
from edgar_client import resolve_cik, fetch_company_facts, flatten_company_financials  # SEC EDGAR XBRL API calls and data flattening
from file_logger import OutputTextWriter  # renamed from outputTextWriter
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
TICKERS: list[str] = ["AAPL", "MSFT", "GOOGL"]  # Must match TICKERS in dashboard/app.py
# ─────────────────────────────────────────────────────────────────────────────


@dag(  # type:ignore
    "Stock_Market_Pipeline",
    default_args={
        "depends_on_past": False,
        "retries": 1,
        "retry_delay": timedelta(minutes=5),
        "execution_timeout": timedelta(minutes=10),  # hard ceiling: kills task if it hangs past this
        'on_failure_callback': on_failure_alert,  # Slack + PVC log on task failure
        'on_success_callback': on_success_alert,  # Slack recovery message + clear alert state
        'on_retry_callback': on_retry_alert,  # Slack + PVC log on task retry
    },
    description="Company financials pipeline: SEC EDGAR XBRL → Kafka (consumer DAG writes Snowflake → dbt)",
    schedule=timedelta(days=1),  # Daily: SEC EDGAR companyfacts updates only when companies file (≈annually)
    # start_date must be in the past for Airflow to schedule the first run immediately
    # Use fixed past date instead of pendulum.now() to prevent DAG configuration drift on each parse
    start_date=pendulum.datetime(2025, 3, 29, 0, 0, tz="America/New_York"),
    # catchup=False: without this, Airflow would try to run one instance per week
    # starting from start_date until today, creating many queued runs on first deploy.
    # We skip that because SEC EDGAR companyfacts already returns all historical data
    # in the very first successful run.
    catchup=False,  # don't backfill historical runs when DAG is first deployed
    tags=["stocks", "sec_edgar", "financials", "snowflake", "portfolio"]
)
def stock_market_pipeline():
    """
    ### Company Financials Data Pipeline

    Pulls financial data (revenue, net income, EPS, assets, etc.) for a list
    of tickers from SEC EDGAR's XBRL API, flattens the nested XBRL JSON into
    a tabular format, and loads it into Snowflake (RAW schema).

    Data source: SEC EDGAR (U.S. government, public domain, no API key needed)

    #### Pipeline stages:
    extract()  →  transform()  →  publish_to_kafka()  →  trigger stock_consumer_pipeline
    (Snowflake write + dbt run in dag_stocks_consumer.py)
    """

    @task()
    def extract() -> str:
        """
        ### Extract
        Fetch raw XBRL financial data for each ticker from SEC EDGAR.
        Stages the full raw payload to the PVC and returns the file path.

        Why return a file path instead of the data directly?
        SEC EDGAR companyfacts responses are 10–15 MB per ticker (~45 MB total).
        Airflow XCom stores values in MariaDB as MEDIUMBLOB (16 MB max), so passing
        the raw payload through XCom causes a silent OOM kill in the transform worker.
        Instead we stage to the PVC (already mounted) and pass only the path — a
        ~100-byte string — through XCom. This is the canonical Airflow pattern for
        large inter-task data.
        """

        # Halt this task (and downstream transform/load) if vacation mode is active
        check_vacation_mode()

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # NOTE: I must declare this inside a @task object so the task only connects to that folder when the task runs.
        # If I had declared this constructor in the main area (outside of a task method etc.), it would run when the DAG is initialized,
        # which would cause issues.

        # Build a unique staging filename from the run_id so concurrent runs don't collide
        context = get_current_context()
        run_id: str = context["run_id"].replace(":", "_").replace("+", "_")  # sanitize for filesystem
        staging_path: str = f"/opt/airflow/out/raw_{run_id}.json"

        results: list[dict[str, Any]] = []

        for ticker in TICKERS:
            writer.log(f"Resolving CIK for: {ticker}")
            # SEC EDGAR uses CIK numbers, not ticker symbols — resolve_cik() handles the mapping
            cik = resolve_cik(ticker)
            writer.log(f"  CIK: {cik}")

            writer.log(f"Fetching company facts for: {ticker}")
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
            writer.log(f"  ✓ {ticker}: {gaap_count} US-GAAP concepts received")

        # Write full payload to PVC — XCom carries only the path string (~100 bytes)
        with open(staging_path, "w") as f:
            json.dump(results, f)
        writer.log(f"Raw data staged to: {staging_path}")

        return staging_path


    @task()
    def transform(staging_path: str) -> list[dict[str, Any]]:
        import pandas as pd  # deferred: avoid slow pandas init during DagBag parse (30s timeout)
        """
        ### Transform
        Flatten each ticker's nested XBRL JSON into a list of row-dicts.
        One row per ticker per financial metric per reporting period.

        Input:  path to PVC staging file written by extract() (tiny XCom string)
        Output shape (to load):  [{ "ticker", "cik", "entity_name", "metric", "label",
                                    "period_end", "value", "filed_date", "form_type",
                                    "fiscal_year", "fiscal_period", "frame" }, ...]
        """

        # Location that the K3S Kubernetes pod (as specified in the PortableVolume) is pointing to inside the K3S Kubernetes pod, which will push
        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")

        # Read the full raw payload from PVC — avoids loading 45 MB through XCom
        with open(staging_path, "r") as f:
            raw_data: list[dict[str, Any]] = json.load(f)

        all_records: list[dict[str, Any]] = []

        for item in raw_data:
            ticker = item["ticker"]
            # flatten_company_financials() lives in edgar_client.py — keeps transform() clean
            rows = flatten_company_financials(ticker, item["raw"], annual_only=True)
            all_records.extend(rows)
            writer.log(f"  {ticker}: {len(rows)} rows after flatten (10-K annual filings only)")

        # Preview the transformed data
        preview_df: pd.DataFrame = pd.DataFrame(all_records)
        writer.log("----Transform Preview----")
        writer.log(str(preview_df.head()))
        writer.log(str(preview_df.dtypes))

        # Remove staging file — data now lives in XCom as compact flat records
        os.remove(staging_path)
        writer.log(f"Staging file cleaned up: {staging_path}")

        # Convert to list-of-dicts so Airflow XCom can serialize it as JSON
        return all_records


    @task()
    def publish_to_kafka(records: list[dict[str, Any]]) -> int:
        """
        ### Publish
        Publish the full transformed batch to Kafka topic stocks.financials.raw.
        Returns record count. The consumer DAG (dag_stocks_consumer.py) handles
        the Snowflake write and dbt run.

        One message per DAG run keyed by run_id for idempotency.
        """
        import json
        from kafka import KafkaProducer  # kafka-python, installed via _PIP_ADDITIONAL_REQUIREMENTS

        writer: OutputTextWriter = OutputTextWriter("/opt/airflow/out")
        context = get_current_context()

        bootstrap: str = Variable.get("KAFKA_BOOTSTRAP_SERVERS")  # kafka.kafka.svc.cluster.local:9092
        producer = KafkaProducer(
            bootstrap_servers=bootstrap,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            max_block_ms=15000,  # fail fast if broker unreachable during send/flush
        )

        # Single message per run — full list-of-dicts as one JSON payload
        producer.send(
            "stocks-financials-raw",
            key=context["run_id"].encode("utf-8"),  # idempotency key: prevents duplicate processing on retry
            value=records,
        )
        producer.flush()   # block until broker acknowledges receipt
        producer.close()

        writer.log(f"Published {len(records)} records to stocks-financials-raw")
        return len(records)


    # ── Wiring the pipeline ───────────────────────────────────────────────────
    # extract → transform → publish_to_kafka → trigger consumer DAG
    # Snowflake write + dbt are handled in dag_stocks_consumer.py
    staging_path: XComArg = extract()
    transformed:  XComArg = transform(staging_path)  # type: ignore[arg-type]
    publish_task          = publish_to_kafka(transformed)  # type: ignore[arg-type]

    # Fire consumer DAG after publish; consumer owns Snowflake write + dbt
    trigger_consumer = TriggerDagRunOperator(
        task_id="trigger_consumer",
        trigger_dag_id="stock_consumer_pipeline",
        wait_for_completion=False,  # fire-and-forget — consumer DAG has its own retries
    )
    publish_task >> trigger_consumer


dag = stock_market_pipeline()
