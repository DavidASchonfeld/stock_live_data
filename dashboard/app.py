import dash
import flask
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

        # ── Revenue & Net Income grouped bar chart ────────────────────────
        dcc.Graph(id="price-chart"),

        # ── Net Income standalone bar chart ───────────────────────────────
        dcc.Graph(id="volume-chart"),

        # ── Summary stats table ───────────────────────────────────────────
        html.Div(id="stats-table", style={"marginTop": "20px"}),
    ]
)

register_routes(app)
register_callbacks(dash_app)


# Runs if you call the script directly
# Does not run when you use Gunicorn to run this script
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
