import plotly.graph_objects as go
from dash import html
from dash.dependencies import Input, Output

from db import _load_ticker_data, load_anomalies  # load_anomalies added for the Data Quality section
from charts import build_revenue_net_income_fig, build_net_income_fig, build_stats_table, build_anomaly_scatter, build_anomaly_table  # anomaly chart builders added


def register_callbacks(dash_app) -> None:
    """Register all Dash callbacks onto the given Dash app instance."""

    @dash_app.callback(
        Output("price-chart", "figure"),    # 1st return value → sets the Revenue+NetIncome grouped bar chart
        Output("volume-chart", "figure"),   # 2nd return value → sets the Net Income standalone bar chart
        Output("stats-table", "children"),  # 3rd return value → sets the stats table's HTML children
        Input("ticker-dropdown", "value"),  # triggers callback when the dropdown selection changes
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

        price_fig  = build_revenue_net_income_fig(ticker, revenue_df, net_income_df)
        volume_fig = build_net_income_fig(ticker, net_income_df)
        stats      = build_stats_table(ticker, revenue_df, net_income_df)

        return price_fig, volume_fig, stats

    # ── Data Quality — Anomaly Detection callback ─────────────────────────────
    @dash_app.callback(
        Output("anomaly-scatter", "figure"),   # 1st return value → updates the scatter plot figure
        Output("anomaly-table", "children"),   # 2nd return value → updates the detail table's HTML children
        Input("anomaly-refresh-btn", "n_clicks"),  # triggers on button click; also fires on initial page load
        prevent_initial_call=False,  # load data immediately on page load, not just on button click
    )
    def update_anomalies(n_clicks):
        """Re-render anomaly scatter and table on page load or when the user clicks Refresh."""
        try:
            df = load_anomalies()  # query Snowflake (or return empty frame for non-Snowflake backends)
        except Exception as e:
            # Show an error annotation in the chart area if Snowflake is unreachable
            empty_fig = go.Figure()
            empty_fig.add_annotation(text=f"DB error: {e}", showarrow=False, font={"size": 14})
            return empty_fig, html.P(f"Could not load anomaly data: {e}", style={"color": "red"})
        return build_anomaly_scatter(df), build_anomaly_table(df)  # chart + table rendered from the same DataFrame
