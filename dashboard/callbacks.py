import plotly.graph_objects as go
from dash import html
from dash.dependencies import Input, Output

from db import _load_ticker_data
from charts import build_revenue_net_income_fig, build_net_income_fig, build_stats_table


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
