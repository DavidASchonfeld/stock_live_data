# Dashboard: Data Quality — Anomaly Detection Section Added

**Date:** 2026-04-11

---

## What Was Added

A new "Data Quality — Anomaly Detection" section was added to the Dash dashboard (`/dashboard/`). It includes:

- A scatter plot: Revenue YoY% (x-axis) vs Net Income YoY% (y-axis), with anomalies shown as red X markers and normal rows as blue dots.
- A detail table: all tickers with their growth rates, anomaly flag (Yes/No), and IsolationForest score. Anomaly rows are highlighted in light red.
- A "Refresh Anomalies" button that re-queries Snowflake on demand. The section also loads automatically on page load.

---

## Why It Was Added

The `anomaly_detector` DAG was already writing IsolationForest results to `PIPELINE_DB.ANALYTICS.FCT_ANOMALIES` in Snowflake, but there was no way to see those results without querying Snowflake directly. The dashboard is the natural place to surface them — the data quality view closes the loop between the ML pipeline and the human reviewing it.

---

## How It Was Implemented

Four files were edited:

1. **`dashboard/db.py`** — Added `load_anomalies()`. It checks the backend guard first (non-Snowflake environments return an empty DataFrame immediately), then checks the in-memory cache (1-hour TTL, same as financials), then queries `PIPELINE_DB.ANALYTICS.FCT_ANOMALIES`. A `try/except` wraps the query so that if the table doesn't exist yet (DAG hasn't run), the dashboard still loads cleanly instead of crashing.

2. **`dashboard/charts.py`** — Added `build_anomaly_scatter()` (two-trace Plotly scatter, one red/one blue) and `build_anomaly_table()` (Dash HTML table with per-row conditional styling). Both functions check `df.empty` at the top and return placeholder UI before the first DAG run.

3. **`dashboard/callbacks.py`** — Added the `update_anomalies` callback. `prevent_initial_call=False` means it fires on page load, not just on button click — so the section is populated immediately when the dashboard opens.

4. **`dashboard/app.py`** — Added the layout block: `html.Hr`, `html.H2`, description `html.P`, `html.Button`, `dcc.Graph`, and `html.Div` for the table, all placed after the existing `stats-table` div.

No new packages were added. No changes to `deploy.sh` were needed — this is purely Python inside `dashboard/`, and the existing deploy pipeline rebuilds the Docker image and redeploys the pod.

---

## How the Fix Was Verified

1. With `DB_BACKEND != "snowflake"` (default local env): the scatter shows "No data yet" and the table shows "No anomaly data yet — run the pipeline to generate results." — no errors.
2. After the `anomaly_detector` DAG runs and populates `FCT_ANOMALIES`: the scatter and table render with real data.
3. Clicking "Refresh Anomalies" re-fires the callback and updates both outputs.
