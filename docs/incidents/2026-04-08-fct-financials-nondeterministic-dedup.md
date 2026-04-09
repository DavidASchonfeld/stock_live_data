# Incident: Non-Deterministic Dedup in fct_company_financials — 2026-04-08

## What happened

The RAW table (`COMPANY_FINANCIALS`) contains duplicates by design — SEC EDGAR returns all historical data on every API call, and a single metric/period can appear under multiple XBRL `frame` values (e.g., `CY2024` and `CY2024Q4`). The `fct_company_financials` dbt model is supposed to clean this up by keeping only the most recently filed row per `(ticker, metric, period_end)`.

The dedup used `ROW_NUMBER() ORDER BY filed_date DESC`. Because `filed_date` is a DATE (not a timestamp), two rows from different frames can share the exact same `filed_date`. When that happens, Snowflake picks a winner arbitrarily — meaning the dashboard value for a given metric could change between dbt runs even though no new data arrived.

## Fix

Added `frame ASC` as a tiebreaker to the `ROW_NUMBER()` ORDER BY in `fct_company_financials.sql`:

```sql
order by filed_date desc nulls last, frame asc nulls last
```

When `filed_date` ties, the lexicographically earlier frame wins (e.g., `CY2024` before `CY2024Q4`). The choice is arbitrary but now consistent and deterministic across every run.

## Test added

Added `assert_fct_financials_no_duplicate_rows.sql` — a singular dbt test that fails if any `(ticker, metric, period_end)` combination has more than one row after dedup. This runs automatically with `dbt test --select tag:stocks` inside `dag_stocks.py`, so a future regression surfaces as a DAG alert rather than a silent bad value.

## Deploy

Run `./scripts/deploy.sh` to sync the updated dbt files to EC2. Changes take effect on the next `dag_stocks.py` run.
