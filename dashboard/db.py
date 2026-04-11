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
        role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),  # explicit role — prevents default role from blocking MARTS table access
    ))
else:
    # MariaDB engine (default) — stays active until DB_BACKEND=snowflake is set
    try:
        DB_ENGINE = create_engine(
            f"mysql+pymysql://{SQL_USERNAME}:{SQL_PASSWORD}@{SQL_URL}/{SQL_DATABASE}"
        )
    except Exception:
        DB_ENGINE = None  # pymysql not installed locally — queries will return empty frames
# Fully-qualified Snowflake name avoids session-schema ambiguity (mirrors FCT_ANOMALIES pattern)
_FINANCIALS_TABLE = "PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS" if DB_BACKEND == "snowflake" else "company_financials"
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
    # Return empty frame if no engine is available (e.g. pymysql not installed locally)
    if DB_ENGINE is None:
        return pd.DataFrame(columns=["metric", "label", "period_end", "value", "fiscal_year", "fiscal_period"])

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


# ── Cache pre-warming ─────────────────────────────────────────────────────────
def prewarm_cache(tickers: list) -> None:
    """Query Snowflake for all tickers + anomalies at startup so the first user request hits the cache, not the DB.

    Called in a background thread from app.py immediately after the Flask container starts.
    Failures are silenced — a cache miss on first request is acceptable; a crash at startup is not.
    """
    for ticker in tickers:
        try:
            _load_ticker_data(ticker)  # populates the per-ticker financials cache entry
        except Exception:
            pass  # non-fatal — spinner will cover the delay if Snowflake is briefly unavailable
    try:
        load_anomalies()  # populates the anomalies cache entry
    except Exception:
        pass
# ─────────────────────────────────────────────────────────────────────────────

# ── Anomaly detection results ─────────────────────────────────────────────────
# Column list defined once so both the guard path and the real query always return
# a DataFrame with the same schema — prevents KeyError in downstream callers.
ANOMALY_COLUMNS = [
    "ticker", "fiscal_year", "revenue_yoy_pct", "net_income_yoy_pct",
    "is_anomaly", "anomaly_score", "detected_at", "mlflow_run_id",
]

def load_anomalies() -> pd.DataFrame:
    """Return anomaly detection scores from FCT_ANOMALIES; empty DataFrame if unavailable.

    Table is created by the first anomaly_detector DAG run, not at deploy time,
    so every code path that can't reach it returns a typed empty DataFrame.
    """
    # Guard: FCT_ANOMALIES only exists in Snowflake — skip the query entirely for other backends
    if DB_BACKEND != "snowflake":
        return pd.DataFrame(columns=ANOMALY_COLUMNS)  # typed empty frame keeps callers from getting None

    # Check in-memory cache before hitting Snowflake — avoids a round-trip on every page load
    cached = _cache_get("anomalies")
    if cached is not None:
        return cached  # cache hit — return immediately without querying the DB

    # Fully-qualified table name avoids any default-schema ambiguity in Snowflake
    query = text("""
        SELECT ticker, fiscal_year, revenue_yoy_pct, net_income_yoy_pct,
               is_anomaly, anomaly_score, detected_at, mlflow_run_id
        FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES
        ORDER BY is_anomaly DESC, anomaly_score ASC
    """)
    try:
        with DB_ENGINE.connect() as conn:
            df = pd.read_sql(query, conn)  # execute query and load all rows into a DataFrame
        _cache_set("anomalies", df, CACHE_TTL_FINANCIALS)  # cache for 1 hour to match financials TTL
        return df
    except Exception:
        # Table may not exist yet if the DAG hasn't run — return empty frame so the dashboard doesn't crash
        return pd.DataFrame(columns=ANOMALY_COLUMNS)
# ─────────────────────────────────────────────────────────────────────────────
