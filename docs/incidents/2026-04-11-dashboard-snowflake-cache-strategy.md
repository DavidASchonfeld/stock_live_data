# Dashboard Snowflake Cache Strategy

**Date:** 2026-04-11

## What the Question Was

The dashboard connects to Snowflake to load financial data and anomaly scores. Concern: does the dashboard still hit Snowflake on every page load, and should MariaDB be added as a persistent cache layer to minimize cost?

## What Was Already in Place

`db.py` has an in-memory cache (`_QUERY_CACHE`) — a plain Python dict in RAM. Both `_load_ticker_data` (financials) and `load_anomalies` (anomaly scores) check this cache before querying Snowflake:
- Financials TTL: 1 hour
- Anomalies TTL: 1 hour (matches financials)

Within a running container, this works correctly. A ticker loaded once is served from RAM for the next hour with zero Snowflake calls.

## The Gap: Cache Resets on Every Deploy

The cache is process-local and non-persistent. Every deploy runs `kubectl delete pod` then `kubectl apply`, which destroys and recreates the container. Fresh container = empty cache = the **first request after any deploy always cold-hits Snowflake** for every ticker and the anomaly table.

This is the only meaningful gap in the existing design.

## Frequency of Snowflake Hits (Before Fix)

| Event | Hits |
|---|---|
| First request after a deploy | 1 per query type per Gunicorn worker |
| TTL expiry (every hour, if no deploy) | 1 per query type per worker |
| Normal user traffic within TTL | 0 — served from RAM |

For a personal project with infrequent deploys and light traffic, the actual dollar cost is negligible (Snowflake XS warehouse bills by the second; a 3-second query costs fractions of a cent). But the cold-start delay (the 5-second Snowflake round-trip) is a real UX problem for whoever loads the page first after a deploy.

## Should MariaDB Be Added as a Persistent Cache?

No. This was evaluated and rejected as over-engineering for this project:
- MariaDB is a relational database, not a cache store
- You'd need a separate sync job (Airflow DAG or cron) to refresh its data
- You'd need to handle schema drift between Snowflake and MariaDB
- The data (SEC financials) changes at most daily, so a 1-hour in-memory TTL is already very conservative
- The root problem (cold start after deploy) is solved more simply below

## The Fix: Startup Cache Pre-Warming

**`db.py`** — added `prewarm_cache(tickers)`: queries `_load_ticker_data` for every ticker and calls `load_anomalies()`. Each call populates the in-memory cache with the same logic the callbacks use. Failures are silenced so a Snowflake outage at startup doesn't crash the container.

**`app.py`** — after registering routes and callbacks, starts `prewarm_cache` in a background daemon thread:

```python
threading.Thread(target=lambda: prewarm_cache(TICKERS), daemon=True).start()
```

`daemon=True` means the thread won't block the container from shutting down. The thread runs in the background while Gunicorn handles requests normally. The `dcc.Loading` spinner (added separately) covers the brief window before the pre-warm completes.

## How This Solves the Problem

- Before: first request after any deploy → Snowflake query → 5-second delay for the user
- After: container starts → pre-warm thread fires immediately → Snowflake query happens in the background → by the time the first user loads the page, the cache is already hot → instant response

The TTL-based expiry (every hour) still triggers one Snowflake hit per worker to refresh stale data, which is expected and correct behavior. The Snowflake warehouse auto-suspends when idle, so non-peak hours cost nothing.
