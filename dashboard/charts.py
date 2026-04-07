import pandas as pd
import plotly.graph_objects as go
from dash import html


def build_revenue_net_income_fig(ticker: str, revenue_df: pd.DataFrame, net_income_df: pd.DataFrame) -> go.Figure:
    """Grouped bar chart: Revenue + Net Income side-by-side per fiscal year.

    Values divided by 1e9 to display in billions (e.g. 394_328_000_000 → 394.33).
    barmode="group" places bars for the same x position next to each other (not stacked).
    """
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=revenue_df["fiscal_year"],
        y=revenue_df["value"] / 1e9,
        name="Revenue",
        marker_color="#3b82f6",  # blue
    ))
    fig.add_trace(go.Bar(
        x=net_income_df["fiscal_year"],
        y=net_income_df["value"] / 1e9,
        name="Net Income",
        marker_color="#10b981",  # green
    ))
    fig.update_layout(
        title=f"{ticker} — Annual Revenue & Net Income",
        xaxis_title="Fiscal Year",
        yaxis_title="USD (Billions)",
        barmode="group",
    )
    return fig


def build_net_income_fig(ticker: str, net_income_df: pd.DataFrame) -> go.Figure:
    """Standalone net income bar chart.

    Separate chart lets the user compare net income magnitude without Revenue dwarfing it.
    """
    fig = go.Figure(data=[go.Bar(
        x=net_income_df["fiscal_year"],
        y=net_income_df["value"] / 1e9,
        name="Net Income",
        marker_color="#10b981",
    )])
    fig.update_layout(
        title=f"{ticker} — Annual Net Income",
        xaxis_title="Fiscal Year",
        yaxis_title="USD (Billions)",
    )
    return fig


def build_stats_table(ticker: str, revenue_df: pd.DataFrame, net_income_df: pd.DataFrame) -> html.Table:
    """Summary stats table: latest annual Revenue and Net Income for the selected ticker.

    html.Table / html.Thead / html.Tbody / html.Tr / html.Th / html.Td are
    Dash HTML components that map directly to standard HTML tags.
    """
    # Pull the most recent annual row for each metric (last row after ORDER BY period_end ASC)
    latest_rev  = revenue_df.iloc[-1]    if len(revenue_df)    > 0 else None
    latest_ni   = net_income_df.iloc[-1] if len(net_income_df) > 0 else None
    latest_year = latest_rev["fiscal_year"] if latest_rev is not None else "N/A"
    rev_str = f"${latest_rev['value']/1e9:.2f}B"  if latest_rev is not None else "N/A"
    ni_str  = f"${latest_ni['value']/1e9:.2f}B"   if latest_ni  is not None else "N/A"

    return html.Table(
        style={"borderCollapse": "collapse", "width": "100%"},
        children=[
            # ── Header row ──────────────────────────────────────────────
            html.Thead(html.Tr([html.Th(c, style={"border": "1px solid #e5e7eb", "padding": "8px", "background": "#f9fafb"})
                for c in ["Ticker", "Latest Fiscal Year", "Revenue", "Net Income"]
            ])),
            # ── Data row ────────────────────────────────────────────────
            html.Tbody(html.Tr([html.Td(v, style={"border": "1px solid #e5e7eb", "padding": "8px"})
                for v in [ticker, latest_year, rev_str, ni_str]
            ])),
        ]
    )
