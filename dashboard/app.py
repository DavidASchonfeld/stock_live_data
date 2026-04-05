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
            "Live data pulled daily by Airflow → stored in MariaDB (→ Snowflake in Step 2).",
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

        # ── Closing price chart ───────────────────────────────────────────
        dcc.Graph(id="price-chart"),

        # ── Volume bar chart ──────────────────────────────────────────────
        dcc.Graph(id="volume-chart"),

        # ── Summary stats table ───────────────────────────────────────────
        html.Div(id="stats-table", style={"marginTop": "20px"}),
    ]
)


def _load_ticker_data(ticker: str) -> pd.DataFrame:
    """Query MariaDB for all rows matching the given ticker, ordered by date.

    Private helper (leading underscore) because it's only called by the Dash
    callback above — not part of the public API of this module.
    A new DB connection is opened per call; SQLAlchemy's connection pool
    handles reuse and cleanup automatically.
    """
    # :ticker is a SQLAlchemy named bind parameter; its value is supplied by params={"ticker": ticker} below
    query = text("SELECT date, open, high, low, close, volume FROM stock_daily_prices WHERE ticker = :ticker ORDER BY date ASC")
    with DB_ENGINE.connect() as conn:
        df = pd.read_sql(query, conn, params={"ticker": ticker})
    # Cast date column to proper datetime so Plotly renders the x-axis correctly
    df["date"] = pd.to_datetime(df["date"])
    return df


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

    # ── Candlestick chart (OHLC) ──────────────────────────────────────────
    # A candlestick chart shows price movement for each time period as a "candle":
    #   - Open  (O): price at the start of the period
    #   - High  (H): highest price reached during the period
    #   - Low   (L): lowest price reached during the period
    #   - Close (C): price at the end of the period
    # The "body" of the candle spans Open→Close; the thin "wicks" extend to High/Low.
    # Green (or hollow) candles = price rose; Red (or filled) candles = price fell.
    price_fig = go.Figure(data=[go.Candlestick(
        x=df["date"],
        open=df["open"],
        high=df["high"],
        low=df["low"],
        close=df["close"],
        name=ticker,
    )])
    # Simple Moving Average (SMA): smooths out daily price noise by averaging the
    # last N closing prices. A 7-day SMA at any given day = mean of that day's
    # close plus the 6 preceding days' closes.
    #
    # .rolling(window=7) — creates a sliding 7-row window that moves one row at a
    #   time through the DataFrame. The first 6 rows produce NaN (not enough data).
    # .mean()            — computes the arithmetic average of each window.
    df["sma_7"] = df["close"].rolling(window=7).mean()

    # go.Scatter is Plotly's general-purpose "connect points" trace.
    # A scatter plot maps two variables onto X/Y axes as individual points;
    # with mode="lines" those points are connected into a continuous line instead
    # of being shown as dots.
    #   x=df["date"]            — the dates go on the horizontal axis (matches the candlestick)
    #   y=df["sma_7"]           — the computed SMA values go on the vertical axis
    #   mode="lines"            — draw a line through the points, not individual markers
    #   name="7-day SMA"        — label shown in the chart legend
    #   line={"color": "orange"} — makes the SMA line orange so it stands out from the candles
    #
    # add_trace() layers this new line on top of the existing candlestick figure
    # (price_fig) without replacing it.
    price_fig.add_trace(go.Scatter(x=df["date"], y=df["sma_7"], mode="lines", name="7-day SMA", line={"color": "orange"}))
    price_fig.update_layout(
        title=f"{ticker} — Daily Close + 7-Day SMA",
        xaxis_title="Date",
        yaxis_title="Price (USD)",
        xaxis_rangeslider_visible=False,  # hide the range slider for cleaner look
    )

    # ── Volume bar chart ──────────────────────────────────────────────────
    volume_fig = go.Figure(data=[go.Bar(x=df["date"], y=df["volume"], name="Volume", marker_color="#3b82f6")])
    volume_fig.update_layout(title=f"{ticker} — Daily Volume", xaxis_title="Date", yaxis_title="Volume")

    # ── Summary stats table ───────────────────────────────────────────────
    latest = df.iloc[-1]  # most recent row

    # html.Table / html.Thead / html.Tbody / html.Tr / html.Th / html.Td are
    # Dash HTML components that map directly to standard HTML tags:
    #   <table>, <thead>, <tbody>, <tr>, <th>, <td>
    # They let you build an HTML table purely in Python instead of writing raw HTML.
    stats = html.Table(
        style={"borderCollapse": "collapse", "width": "100%"},
        children=[
            # ── Header row ──────────────────────────────────────────────
            # html.Thead wraps the <thead> section; html.Tr is one table row.
            # `c` gets its value by simply stepping through the literal list below,
            # one item at a time, left to right:
            #   1st iteration: c = "Ticker"      → builds html.Th("Ticker")
            #   2nd iteration: c = "Latest Date" → builds html.Th("Latest Date")
            #   ... and so on for every string in the list.
            # `c` has no other source — it is not a parameter, not defined elsewhere.
            # The name `c` (convention for "column") only exists during the loop.
            html.Thead(html.Tr([html.Th(c, style={"border": "1px solid #e5e7eb", "padding": "8px", "background": "#f9fafb"})
                for c in [
                    "Ticker", "Latest Date", "Open", "High", "Low", "Close", "Volume"
                ]
            ])),

            # ── Data row ────────────────────────────────────────────────
            # html.Tbody wraps the <tbody> section.
            # `v` gets its value by simply stepping through the list below,
            # one item at a time, left to right:
            #   1st iteration: v = ticker                      → builds html.Td(ticker)
            #   2nd iteration: v = str(latest["date"].date())  → builds html.Td("2025-08-07")
            #   3rd iteration: v = f"${latest['open']:.2f}"   → builds html.Td("$173.40")
            #   ... and so on for every item in the list.
            # `v` has no other source — it is not a parameter, not defined elsewhere.
            # The name `v` (convention for "value") only exists during the loop.
            # The items in the list come from two places:
            #   - `ticker`: the stock symbol passed in as the callback input argument
            #   - `latest[...]`: fields from the last DataFrame row (defined on line 185)
            html.Tbody(html.Tr([html.Td(v, style={"border": "1px solid #e5e7eb", "padding": "8px"})
                for v in [
                    ticker,
                    str(latest["date"].date()),
                    # f"${latest['open']:.2f}" — the :.2f format spec means:
                    #   : = start of format specification
                    #   .2 = round to 2 decimal places
                    #   f  = format as a fixed-point (decimal) number
                    # Result example: 173.4 → "$173.40"
                    f"${latest['open']:.2f}",
                    f"${latest['high']:.2f}",
                    f"${latest['low']:.2f}",
                    f"${latest['close']:.2f}",
                    f"{int(latest['volume']):,}",  # :, adds thousands separator  e.g. 1234567 → "1,234,567"
                ]
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
            # Validate stock_daily_prices table
            # COUNT(*) detects if data is flowing in; row count trends indicate pipeline health
            stock_count = conn.execute(text("SELECT COUNT(*) FROM stock_daily_prices")).scalar()
            # MAX(date) shows data freshness; stale dates indicate pipeline failure
            stock_latest = conn.execute(text("SELECT MAX(date) FROM stock_daily_prices")).scalar()
            # Sample 5 rows to catch schema changes or data corruption (bad prices, wrong formats)
            stock_sample = pd.read_sql(
                text("SELECT * FROM stock_daily_prices ORDER BY date DESC LIMIT 5"),
                conn
            )
            # Convert count to int (SQL returns generic object); dates/data to string for JSON serialization
            validation_info["tables"]["stock_daily_prices"] = {
                "row_count": int(stock_count),
                "latest_date": str(stock_latest),
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
