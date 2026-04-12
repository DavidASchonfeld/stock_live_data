import pandas as pd
import plotly.graph_objects as go
from dash import html


def build_temperature_fig(df: pd.DataFrame) -> go.Figure:
    """Line chart of hourly temperature (°F) over the last 7 days.

    Single trace keeps the chart readable; blue matches the existing dashboard palette.
    """
    # Guard: return an annotated empty figure if no data has arrived from the pipeline yet
    if df.empty:
        fig = go.Figure()
        fig.add_annotation(text="No weather data yet", showarrow=False, font={"size": 14})  # placeholder so the chart area isn't blank
        return fig

    fig = go.Figure(data=[go.Scatter(
        x=df["observation_time"],       # hourly timestamps on the x-axis
        y=df["temperature_f"],          # temperature in Fahrenheit on the y-axis
        mode="lines",                   # continuous line (no dots) — cleaner for dense hourly data
        name="Temperature (°F)",
        line={"color": "#3b82f6", "width": 2},  # blue matches the stocks dashboard palette
        hovertemplate="%{x}<br>%{y:.1f}°F<extra></extra>",  # clean tooltip showing time + temp
    )])
    fig.update_layout(
        title="7-Day Hourly Temperature (°F)",
        xaxis_title="Date / Time",     # label tells the viewer the x-axis is time
        yaxis_title="Temperature (°F)",  # label clarifies the unit
        hovermode="x unified",          # unified hover shows all traces at the same x position
    )
    return fig


def build_weather_stats_table(df: pd.DataFrame):
    """Summary stats table: current temp, 24-hour min/max, and location metadata.

    Filters to the last 24 hours for min/max so the values stay relevant to today.
    Returns html.P placeholder if no data is available yet.
    """
    # Guard: show a friendly message instead of an empty or broken table
    if df.empty:
        return html.P("No weather data yet — run the pipeline to generate results.")

    # Latest row gives current temperature and location metadata
    latest = df.iloc[-1]
    current_temp = f"{latest['temperature_f']:.1f}°F"  # one decimal place is enough precision
    location = f"{latest['latitude']:.1f}°N, {latest['longitude']:.1f}°E"  # human-readable coordinates
    elevation = f"{latest['elevation']:.0f} m"          # elevation in whole meters
    timezone = str(latest["timezone"])                   # IANA timezone string from Open-Meteo

    # Filter to last 24 hours to compute today's min and max temperature
    cutoff = df["observation_time"].max() - pd.Timedelta(hours=24)  # 24-hour window relative to latest timestamp
    last_24h = df[df["observation_time"] >= cutoff]                  # slice to the 24-hour window
    temp_min = f"{last_24h['temperature_f'].min():.1f}°F"           # coldest reading in the window
    temp_max = f"{last_24h['temperature_f'].max():.1f}°F"           # warmest reading in the window

    # ── Header ────────────────────────────────────────────────────────────────
    header_cols = ["Current Temp", "24h Min", "24h Max", "Location", "Elevation", "Timezone"]
    header = html.Thead(html.Tr([
        html.Th(c, style={"border": "1px solid #e5e7eb", "padding": "8px", "background": "#f9fafb"})
        for c in header_cols
    ]))

    # ── Single data row ───────────────────────────────────────────────────────
    cells = [current_temp, temp_min, temp_max, location, elevation, timezone]
    body = html.Tbody(html.Tr([
        html.Td(v, style={"border": "1px solid #e5e7eb", "padding": "8px"})
        for v in cells
    ]))

    return html.Table(
        style={"borderCollapse": "collapse", "width": "100%"},
        children=[header, body],
    )
