import os
import time

import pandas as pd
from dotenv import load_dotenv  # reads .env for local dev; no-op in production
from sqlalchemy import create_engine, text

load_dotenv()

# ── Database connection ───────────────────────────────────────────────────────
# Credentials come from environment variables — this file never contains secrets.
# Local dev:   set values in a .env file at the repo root (gitignored)
# Production:  set values in a Kubernetes Secret referenced by the Flask Deployment
# Step 2 swap: only the env var values change — this code stays identical for Snowflake
SQL_USERNAME = os.environ.get("DB_USER",     "airflow_user")
SQL_PASSWORD = os.environ.get("DB_PASSWORD", "")
SQL_DATABASE = os.environ.get("DB_NAME",     "database_one")
SQL_URL      = os.environ.get("DB_HOST",     "")

DB_BACKEND = os.environ.get("DB_BACKEND", "mariadb")  # "mariadb" (default) or "snowflake" — switch after validating Snowflake data
if DB_BACKEND == "snowflake":
    # Snowflake engine — set DB_BACKEND=snowflake in the K8s secret to activate
    from snowflake.sqlalchemy import URL as SnowflakeURL
    DB_ENGINE = create_engine(SnowflakeURL(
        account=os.environ.get("SNOWFLAKE_ACCOUNT"),
        user=os.environ.get("SNOWFLAKE_USER"),
        password=os.environ.get("SNOWFLAKE_PASSWORD"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "PIPELINE_DB"),
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "MARTS"),  # dashboard reads MARTS, not RAW
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "PIPELINE_WH"),
    ))
else:
    # MariaDB engine (default) — stays active until DB_BACKEND=snowflake is set
    DB_ENGINE = create_engine(
        f"mysql+pymysql://{SQL_USERNAME}:{SQL_PASSWORD}@{SQL_URL}/{SQL_DATABASE}"
    )
# Table name differs by backend — Snowflake uses the dbt MARTS output table
_FINANCIALS_TABLE = "FCT_COMPANY_FINANCIALS" if DB_BACKEND == "snowflake" else "company_financials"
# ─────────────────────────────────────────────────────────────────────────────

# ── Query cache (cost optimization #2) ───────────────────────────────────────
CACHE_TTL_FINANCIALS = 3600   # 1 hour — SEC filings change at most daily
CACHE_TTL_WEATHER    = 900    # 15 min — reserved for future weather queries
_QUERY_CACHE: dict = {}       # {key: (dataframe, expires_at)}

def _cache_get(key: str):
    """Return cached value if present and not expired, else None."""
    entry = _QUERY_CACHE.get(key)
    if entry and time.monotonic() < entry[1]:
        return entry[0]
    return None

def _cache_set(key: str, value, ttl: int) -> None:
    """Store value with a monotonic expiry timestamp."""
    _QUERY_CACHE[key] = (value, time.monotonic() + ttl)
# ─────────────────────────────────────────────────────────────────────────────


def _load_ticker_data(ticker: str) -> pd.DataFrame:
    """Query MariaDB for annual Revenue and Net Income rows from company_financials.

    Private helper (leading underscore) because it's only called by the Dash
    callback — not part of the public API of this module.
    A new DB connection is opened per call; SQLAlchemy's connection pool
    handles reuse and cleanup automatically.
    Filters to fiscal_period='FY' to return one row per metric per annual filing.
    """
    # Return cached result if still fresh
    cache_key = f"financials:{ticker}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # :ticker is a SQLAlchemy named bind parameter; its value is supplied by params={"ticker": ticker} below
    # _FINANCIALS_TABLE is a hardcoded constant (not user input) so f-string is safe here
    query = text(f"""
        SELECT metric, label, period_end, value, fiscal_year, fiscal_period
        FROM {_FINANCIALS_TABLE}
        WHERE ticker = :ticker
          AND metric IN ('Revenues', 'NetIncomeLoss')
          AND fiscal_period = 'FY'
        ORDER BY period_end ASC
    """)
    with DB_ENGINE.connect() as conn:
        df = pd.read_sql(query, conn, params={"ticker": ticker})
    # Cast period_end to datetime so Plotly renders the x-axis correctly
    df["period_end"] = pd.to_datetime(df["period_end"])
    _cache_set(cache_key, df, CACHE_TTL_FINANCIALS)
    return df


# Stub: wire up when stock_daily_prices DAG is added in Step 2
def _load_ohlcv_data(ticker: str) -> pd.DataFrame:  # noqa: ARG001
    """Placeholder for OHLCV price query — not yet called.

    When a DAG that populates stock_daily_prices (OHLCV) is implemented in Step 2,
    wire this function into update_charts() to restore the candlestick chart.
    """
    raise NotImplementedError("stock_daily_prices DAG not yet implemented (Step 2)")
