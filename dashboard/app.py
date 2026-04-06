import os

# ── Architecture: Why Flask + Dash together? ──────────────────────────────────
# Dash is a Python framework for interactive data dashboards built on top of
# Flask, React, and Plotly. Because Dash is built on Flask, a Dash app IS a
# Flask app — they share the same WSGI server (Gunicorn) and the same process.
#
# Why Gunicorn (production) instead of Flask's built-in dev server?
# Flask's dev server is single-threaded and not designed for concurrent requests.
# Gunicorn spawns multiple worker processes (set to 2 in the Dockerfile) so the
# app can handle multiple users loading the dashboard at the same time.
#
# How the two frameworks are combined here:
#   1. Create a plain Flask `app` first.
#   2. Create a Dash `dash_app` that mounts ONTO the Flask app (server=app).
#   3. Dash registers its own routes under /dashboard/; Flask handles the rest.
#   4. Gunicorn is pointed at `app` (the Flask object), which already contains Dash.
# ─────────────────────────────────────────────────────────────────────────────

from dotenv import load_dotenv  # reads .env for local dev; no-op in production
from flask import Flask

import dash
from dash import dcc, html
from dash.dependencies import Input, Output
import plotly.graph_objects as go

import pandas as pd
import sqlalchemy
from sqlalchemy import create_engine, text  # text() required for raw SQL in SQLAlchemy 2.x

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
        schema=os.environ.get("SNOWFLAKE_SCHEMA", "RAW"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "PIPELINE_WH"),
    ))
else:
    # MariaDB engine (default) — stays active until DB_BACKEND=snowflake is set
    DB_ENGINE = create_engine(
        f"mysql+pymysql://{SQL_USERNAME}:{SQL_PASSWORD}@{SQL_URL}/{SQL_DATABASE}"
    )
# ─────────────────────────────────────────────────────────────────────────────


# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)
# ─────────────────────────────────────────────────────────────────────────────


# ── Dash app — mounted on the Flask server at /dashboard/ ────────────────────
# Dash is built on Flask, so it can share the same Gunicorn process
dash_app = dash.Dash(
    __name__,
    server=app,           # attach Dash to our existing Flask instance
    url_base_pathname="/dashboard/",
)

TICKERS = ["AAPL", "MSFT", "GOOGL"]  # must match the tickers loaded by the Airflow DAG

dash_app.layout = html.Div(
    style={"fontFamily": "Arial, sans-serif", "maxWidth": "1100px", "margin": "0 auto", "padding": "20px"},
    children=[

        html.H1("Stock Market Analytics Pipeline", style={"color": "#1f2937"}),
        html.P(
            "SEC EDGAR financial data pulled daily by Airflow → stored in MariaDB (→ Snowflake in Step 2).",
            style={"color": "#6b7280"}
        ),

        # ── Ticker selector ───────────────────────────────────────────────
        html.Label("Select Ticker:", style={"fontWeight": "bold"}),
        dcc.Dropdown(
            id="ticker-dropdown",
            options=[{"label": t, "value": t} for t in TICKERS],
            value="AAPL",          # default selection
            clearable=False,
            style={"width": "200px", "marginBottom": "20px"},
        ),

        # ── Revenue & Net Income grouped bar chart ────────────────────────
        dcc.Graph(id="price-chart"),

        # ── Net Income standalone bar chart ───────────────────────────────
        dcc.Graph(id="volume-chart"),

        # ── Summary stats table ───────────────────────────────────────────
        html.Div(id="stats-table", style={"marginTop": "20px"}),
    ]
)


def _load_ticker_data(ticker: str) -> pd.DataFrame:
    """Query MariaDB for annual Revenue and Net Income rows from company_financials.

    Private helper (leading underscore) because it's only called by the Dash
    callback above — not part of the public API of this module.
    A new DB connection is opened per call; SQLAlchemy's connection pool
    handles reuse and cleanup automatically.
    Filters to fiscal_period='FY' to return one row per metric per annual filing.
    """
    # :ticker is a SQLAlchemy named bind parameter; its value is supplied by params={"ticker": ticker} below
    query = text("""
        SELECT metric, label, period_end, value, fiscal_year, fiscal_period
        FROM company_financials
        WHERE ticker = :ticker
          AND metric IN ('Revenues', 'NetIncomeLoss')
          AND fiscal_period = 'FY'
        ORDER BY period_end ASC
    """)
    with DB_ENGINE.connect() as conn:
        df = pd.read_sql(query, conn, params={"ticker": ticker})
    # Cast period_end to datetime so Plotly renders the x-axis correctly
    df["period_end"] = pd.to_datetime(df["period_end"])
    return df


# Stub: wire up when stock_daily_prices DAG is added in Step 2
def _load_ohlcv_data(ticker: str) -> pd.DataFrame:  # noqa: ARG001
    """Placeholder for OHLCV price query — not yet called.

    When a DAG that populates stock_daily_prices (OHLCV) is implemented in Step 2,
    wire this function into update_charts() to restore the candlestick chart.
    """
    raise NotImplementedError("stock_daily_prices DAG not yet implemented (Step 2)")


@dash_app.callback(
    Output("price-chart", "figure"),    # 1st return value → sets the candlestick chart's figure
    Output("volume-chart", "figure"),   # 2nd return value → sets the volume bar chart's figure
    Output("stats-table", "children"),  # 3rd return value → sets the stats table's HTML children
    Input("ticker-dropdown", "value"),  # triggers the callback when the dropdown selection changes; passes new value as `ticker`
)
def update_charts(ticker: str):
    """Re-render all three outputs whenever the user picks a different ticker.

    Dash callbacks are reactive: Dash automatically calls this function whenever
    any Input component changes. The return values are mapped positionally to the
    Output components defined above — order matters.
    """
    try:
        df = _load_ticker_data(ticker)
    except Exception as e:
        # Show an error message in the chart area if the DB is unreachable
        empty_fig = go.Figure()
        empty_fig.add_annotation(text=f"DB error: {e}", showarrow=False, font={"size": 14})
        return empty_fig, empty_fig, html.P(f"Could not load data: {e}", style={"color": "red"})

    # Split into per-metric DataFrames for separate traces
    revenue_df    = df[df["metric"] == "Revenues"].copy()
    net_income_df = df[df["metric"] == "NetIncomeLoss"].copy()

    # ── Revenue & Net Income grouped bar chart ────────────────────────────
    # go.Bar groups two traces (Revenue + Net Income) side-by-side per fiscal year.
    # Values are divided by 1e9 to display in billions (e.g. 394_328_000_000 → 394.33).
    # barmode="group" places bars for the same x position next to each other (not stacked).
    price_fig = go.Figure()
    price_fig.add_trace(go.Bar(
        x=revenue_df["fiscal_year"],
        y=revenue_df["value"] / 1e9,
        name="Revenue",
        marker_color="#3b82f6",  # blue
    ))
    price_fig.add_trace(go.Bar(
        x=net_income_df["fiscal_year"],
        y=net_income_df["value"] / 1e9,
        name="Net Income",
        marker_color="#10b981",  # green
    ))
    price_fig.update_layout(
        title=f"{ticker} — Annual Revenue & Net Income",
        xaxis_title="Fiscal Year",
        yaxis_title="USD (Billions)",
        barmode="group",
    )

    # ── Net Income standalone bar chart ───────────────────────────────────
    # Separate chart lets the user compare net income magnitude without Revenue dwarfing it.
    volume_fig = go.Figure(data=[go.Bar(
        x=net_income_df["fiscal_year"],
        y=net_income_df["value"] / 1e9,
        name="Net Income",
        marker_color="#10b981",
    )])
    volume_fig.update_layout(
        title=f"{ticker} — Annual Net Income",
        xaxis_title="Fiscal Year",
        yaxis_title="USD (Billions)",
    )

    # ── Summary stats table ───────────────────────────────────────────────
    # Pull the most recent annual row for each metric (last row after ORDER BY period_end ASC)
    latest_rev = revenue_df.iloc[-1]    if len(revenue_df)    > 0 else None
    latest_ni  = net_income_df.iloc[-1] if len(net_income_df) > 0 else None
    latest_year = latest_rev["fiscal_year"] if latest_rev is not None else "N/A"
    rev_str = f"${latest_rev['value']/1e9:.2f}B"   if latest_rev is not None else "N/A"
    ni_str  = f"${latest_ni['value']/1e9:.2f}B"    if latest_ni  is not None else "N/A"

    # html.Table / html.Thead / html.Tbody / html.Tr / html.Th / html.Td are
    # Dash HTML components that map directly to standard HTML tags:
    #   <table>, <thead>, <tbody>, <tr>, <th>, <td>
    # They let you build an HTML table purely in Python instead of writing raw HTML.
    stats = html.Table(
        style={"borderCollapse": "collapse", "width": "100%"},
        children=[
            # ── Header row ──────────────────────────────────────────────
            # `c` steps through the column name list left-to-right, one per iteration
            html.Thead(html.Tr([html.Th(c, style={"border": "1px solid #e5e7eb", "padding": "8px", "background": "#f9fafb"})
                for c in ["Ticker", "Latest Fiscal Year", "Revenue", "Net Income"]
            ])),

            # ── Data row ────────────────────────────────────────────────
            # `v` steps through the value list left-to-right, one per iteration
            html.Tbody(html.Tr([html.Td(v, style={"border": "1px solid #e5e7eb", "padding": "8px"})
                for v in [ticker, latest_year, rev_str, ni_str]
            ])),
        ]
    )

    return price_fig, volume_fig, stats
# ─────────────────────────────────────────────────────────────────────────────


# ── Flask routes ──────────────────────────────────────────────────────────────
@app.route('/')
@app.route('/index')
def index():
    # Redirect root to the Dash dashboard
    return (
        '<h2>Stock Market Analytics Pipeline</h2>'
        '<p>Visit <a href="/dashboard/">the dashboard</a> to see live stock charts.</p>'
    )

@app.route('/hello')
def hello():
    return "Hello!"

@app.route('/health')
def health():
    # Health-check endpoint — useful for Kubernetes liveness probes
    # No DB call needed; fast, reliable signal that pod process is running
    return {"status": "ok"}, 200


@app.route('/validation')
def validation():
    # Data validation endpoint — shows table schemas, row counts, and freshness
    # Used for monitoring: detect when DAGs fail or data stops flowing
    try:
        validation_info = {
            "status": "ok",
            # Include timestamp so caller knows when data was sampled
            "timestamp": pd.Timestamp.now().isoformat(),
            "tables": {}
        }

        with DB_ENGINE.connect() as conn:
            # Validate company_financials table (SEC EDGAR data written by dag_stocks.py)
            # COUNT(*) detects if data is flowing in; row count trends indicate pipeline health
            stock_count = conn.execute(text("SELECT COUNT(*) FROM company_financials")).scalar()
            # MAX(period_end) shows data freshness; stale dates indicate pipeline failure
            stock_latest = conn.execute(text("SELECT MAX(period_end) FROM company_financials")).scalar()
            # Sample 5 rows to catch schema changes or data corruption
            stock_sample = pd.read_sql(
                text("SELECT * FROM company_financials ORDER BY period_end DESC LIMIT 5"),
                conn
            )
            # Convert count to int (SQL returns generic object); dates/data to string for JSON serialization
            validation_info["tables"]["company_financials"] = {
                "row_count": int(stock_count),
                "latest_period_end": str(stock_latest),
                # to_dict('records') converts DataFrame rows to list of dicts (JSON-friendly format)
                "sample_data": stock_sample.to_dict('records') if len(stock_sample) > 0 else []
            }

            # Validate weather_hourly table
            # Same checks as stocks, but MAX(time) instead of MAX(date) for hourly data
            weather_count = conn.execute(text("SELECT COUNT(*) FROM weather_hourly")).scalar()
            weather_latest = conn.execute(text("SELECT MAX(time) FROM weather_hourly")).scalar()
            # Sample shows recent hourly data; helps spot missing hours or corrupted records
            weather_sample = pd.read_sql(
                text("SELECT * FROM weather_hourly ORDER BY time DESC LIMIT 5"),
                conn
            )
            validation_info["tables"]["weather_hourly"] = {
                "row_count": int(weather_count),
                "latest_time": str(weather_latest),
                "sample_data": weather_sample.to_dict('records') if len(weather_sample) > 0 else []
            }

        return validation_info, 200

    # Catch DB connection errors, table-not-found, query errors; return diagnostic message
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
# ─────────────────────────────────────────────────────────────────────────────


# Runs if you call the script directly
# Does not run when you use Gunicorn to run this script
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
