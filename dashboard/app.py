import dash
import flask
import threading  # used to run cache pre-warming without blocking app startup
from dash import dcc, html
from flask import Flask

# ── Architecture: Why Flask + Dash together? ──────────────────────────────────
# Dash is a Python framework for interactive data dashboards built on top of
# Flask, React, and Plotly. Because Dash is built on Flask, a Dash app IS a
# Flask app — they share the same WSGI server (Gunicorn) and the same process.
#
# How the two frameworks are combined here:
#   1. Create a plain Flask `app` first.
#   2. Create a Dash `dash_app` that mounts ONTO the Flask app (server=app).
#   3. Dash registers its own routes under /dashboard/; Flask handles the rest.
#   4. Gunicorn is pointed at `app` (the Flask object), which already contains Dash.
# ─────────────────────────────────────────────────────────────────────────────

from routes import register_routes
from callbacks import register_callbacks
from db import prewarm_cache  # imported here to fire pre-warming without going through the callback layer

app = Flask(__name__)

# Dash mounted on the Flask server at /dashboard/
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

        # dcc.Loading wraps all financials outputs — shows a spinner immediately
        # while the Snowflake query runs so the page never looks broken or blank
        dcc.Loading(
            id="loading-financials",
            type="circle",  # circle spinner — clean, unobtrusive visual cue
            children=[
                # ── Revenue & Net Income grouped bar chart ────────────────
                dcc.Graph(id="price-chart"),

                # ── Net Income standalone bar chart ───────────────────────
                dcc.Graph(id="volume-chart"),

                # ── Summary stats table ───────────────────────────────────
                html.Div(id="stats-table", style={"marginTop": "20px"}),
            ]
        ),

        # ── Data Quality — Anomaly Detection ─────────────────────────────
        html.Hr(),  # visual separator between the financials section and anomaly section
        html.H2("Data Quality — Anomaly Detection", style={"color": "#1f2937", "marginTop": "30px"}),
        html.P(
            # one-sentence description of the model and where results are tracked
            "IsolationForest model scores each ticker's YoY growth; outliers flagged as anomalies and tracked in MLflow.",
            style={"color": "#6b7280"},
        ),
        html.Button(
            "Refresh Anomalies",
            id="anomaly-refresh-btn",  # id referenced by the update_anomalies callback in callbacks.py
            n_clicks=0,
            style={"marginBottom": "20px"},
        ),
        # dcc.Loading wraps anomaly outputs — same pattern as financials section above
        dcc.Loading(
            id="loading-anomalies",
            type="circle",  # consistent spinner style across both sections
            children=[
                dcc.Graph(id="anomaly-scatter"),  # populated by update_anomalies callback — scatter of YoY growth colored by anomaly flag
                html.Div(id="anomaly-table", style={"marginTop": "20px"}),  # populated by update_anomalies callback — detail table
            ]
        ),
    ]
)

register_routes(app)
register_callbacks(dash_app)

# Pre-warm the cache in a background thread immediately after startup — Snowflake is queried
# once here so every subsequent user request hits the in-memory cache instead of the DB.
# daemon=True means this thread won't block the process from shutting down if it's still running.
threading.Thread(target=lambda: prewarm_cache(TICKERS), daemon=True).start()


# Runs if you call the script directly
# Does not run when you use Gunicorn to run this script
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, debug=True)
