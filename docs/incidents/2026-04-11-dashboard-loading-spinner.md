# Dashboard Loading Delay — Spinner Fix

**Date:** 2026-04-11

## What Happened

On page load, the dashboard showed a completely blank screen for ~5 seconds before any charts or tables appeared. There was no visual feedback, so it looked like the page had crashed or the server was down.

## Root Cause

Dash fires all callbacks immediately on page load. Both `update_charts` (financials) and `update_anomalies` (anomaly detection) run synchronously and block on Snowflake queries. Until those queries return, Dash leaves the output components empty — no spinner, no message, nothing. Snowflake's compute warehouse also needs a moment to wake up if it has auto-suspended, which explains why the delay could stretch to 5+ seconds on a cold start.

## How It Was Identified

Observed directly: after navigating to `/dashboard/`, all chart areas were blank for several seconds before populating. The delay was consistent and always tied to the first page load (subsequent loads were instant because the in-memory cache in `db.py` had already been populated).

## What Was Fixed

**`app.py`** — wrapped all Dash output components in `dcc.Loading`:
- A `dcc.Loading(type="circle")` now wraps the three financials outputs (`price-chart`, `volume-chart`, `stats-table`).
- A second `dcc.Loading(type="circle")` wraps the two anomaly outputs (`anomaly-scatter`, `anomaly-table`).

**`callbacks.py`** — added `try/except` to `update_anomalies` to match the error-handling pattern already in `update_charts`. If Snowflake is unreachable, the chart shows a red error annotation instead of silently staying blank.

## Why This Fix

`dcc.Loading` is Dash's built-in, idiomatic solution for this exact problem. It tracks when any child component is waiting for a callback to complete and renders a spinner in its place. No extra state management, no polling, no custom JS — just wrapping the outputs. This is the approach recommended by the Plotly/Dash documentation.

A hard query timeout was considered but rejected: if the Snowflake warehouse is auto-suspended, it legitimately needs time to resume. A timeout would turn a slow-but-successful load into a visible error, which is worse UX than a spinner.

## How the Fix Solves the Problem

Before: user sees a blank page → assumes something is broken.  
After: user sees a spinner immediately → understands data is loading → charts appear when ready. If the DB is actually unreachable, a red error message appears instead of an indefinite blank.

The existing 1-hour in-memory cache (`_QUERY_CACHE` in `db.py`) means the spinner only appears on the very first load per server process restart. All subsequent page loads hit the cache and render instantly.
