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


# ── Anomaly detection charts ──────────────────────────────────────────────────

def build_anomaly_scatter(df: pd.DataFrame) -> go.Figure:
    """Scatter plot of Revenue YoY% vs Net Income YoY%, colored red/blue by anomaly flag.

    Two separate traces (anomaly vs normal) so Plotly renders a proper color legend.
    """
    # Guard: return an annotated empty figure before the first DAG run populates the table
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No data yet", showarrow=False, font={"size": 14})  # placeholder so the chart area isn't blank
        return fig

    # Split into two sub-DataFrames so each gets its own color and legend entry
    anomalies  = df[df["is_anomaly"] == True]   # noqa: E712 — rows flagged by IsolationForest
    normals    = df[df["is_anomaly"] == False]   # noqa: E712 — rows within expected range

    fig = go.Figure()

    # Normal points plotted first so anomaly markers render on top
    fig.add_trace(go.Scatter(
        x=normals["revenue_yoy_pct"],
        y=normals["net_income_yoy_pct"],
        mode="markers",
        name="Normal",
        marker={"color": "#3b82f6", "size": 8},  # blue — matches existing chart palette
        hovertext=normals["ticker"] + " " + normals["fiscal_year"].astype(str),  # tooltip shows ticker + year
        hoverinfo="text+x+y",
    ))

    # Anomaly points in red so they stand out immediately
    fig.add_trace(go.Scatter(
        x=anomalies["revenue_yoy_pct"],
        y=anomalies["net_income_yoy_pct"],
        mode="markers",
        name="Anomaly",
        marker={"color": "#ef4444", "size": 10, "symbol": "x"},  # red X marker for visual salience
        hovertext=anomalies["ticker"] + " " + anomalies["fiscal_year"].astype(str),
        hoverinfo="text+x+y",
    ))

    fig.update_layout(
        title="Anomaly Detection — YoY Growth",
        xaxis_title="Revenue YoY %",   # horizontal axis = revenue year-over-year growth
        yaxis_title="Net Income YoY %",  # vertical axis = net income year-over-year growth
    )
    return fig


def build_anomaly_table(df: pd.DataFrame):
    """HTML table listing all tickers with anomaly rows highlighted in light red.

    Anomaly rows sorted first (ORDER BY is_anomaly DESC in the SQL query).
    Returns html.P placeholder when no data is available yet.
    """
    # Guard: show a friendly message instead of an empty table before the DAG has run
    if df.empty:
        return html.P("No anomaly data yet — run the pipeline to generate results.")

    # ── Header ────────────────────────────────────────────────────────────────
    header_cols = ["Ticker", "Fiscal Year", "Revenue YoY%", "Net Income YoY%", "Anomaly", "Score"]
    header = html.Thead(html.Tr([
        html.Th(c, style={"border": "1px solid #e5e7eb", "padding": "8px", "background": "#f9fafb"})
        for c in header_cols
    ]))

    # ── Body rows ─────────────────────────────────────────────────────────────
    rows = []
    for _, row in df.iterrows():
        # Highlight anomaly rows with a light red background to draw attention
        row_style = {"backgroundColor": "#fef2f2"} if row["is_anomaly"] else {}
        cell_style = {"border": "1px solid #e5e7eb", "padding": "8px"}

        cells = [
            html.Td(row["ticker"],                                    style=cell_style),
            html.Td(str(row["fiscal_year"]),                          style=cell_style),
            html.Td(f"{row['revenue_yoy_pct']:.1f}%",                style=cell_style),  # 1 decimal place for readability
            html.Td(f"{row['net_income_yoy_pct']:.1f}%",             style=cell_style),
            html.Td("Yes" if row["is_anomaly"] else "No",            style=cell_style),
            html.Td(f"{row['anomaly_score']:.3f}",                   style=cell_style),  # 3 decimal places matches MLflow precision
        ]
        rows.append(html.Tr(cells, style=row_style))

    return html.Table(
        style={"borderCollapse": "collapse", "width": "100%"},
        children=[header, html.Tbody(rows)],
    )
# ─────────────────────────────────────────────────────────────────────────────
