# Incident: Dashboard Financials Charts Empty — Snowflake Role + Unqualified Table Name

**Date:** 2026-04-11
**Severity:** Medium — anomaly section worked; financials section completely broken

---

## What Happened

After deploying the anomaly detection dashboard section, the page loaded but only the anomaly scatter and table had data. The revenue/net income charts and stats table showed a red error:

> Could not load data: (snowflake.connector.errors.ProgrammingError) 002003 (42S02):
> SQL compilation error: Object 'FCT_COMPANY_FINANCIALS' does not exist or not authorized.

---

## How It Was Identified

The contrast was the first clue: anomalies loaded fine, but financials failed. Both use the same Snowflake engine in `dashboard/db.py`. Comparing the two queries revealed the difference:

- **Anomalies** (working): `FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES` — fully-qualified 3-part name
- **Financials** (broken): `FROM FCT_COMPANY_FINANCIALS` — unqualified, relies on session defaults

Two problems were visible in the code:

1. The `SnowflakeURL` in `db.py` had no `role=` parameter, so Snowflake used the user's default role instead of `PIPELINE_ROLE`. `anomaly_detector.py` hardcodes `role=PIPELINE_ROLE`; the dashboard never did.

2. The financials table name was bare (`FCT_COMPANY_FINANCIALS`), relying on the session's default schema resolving to `MARTS`. Snowflake does not always honor `schema=` in the SQLAlchemy URL as a reliable session default — the fully-qualified path is the safe approach.

Because the anomaly table used a fully-qualified name, it bypassed both problems. The financials table name relied on session defaults that didn't hold.

---

## Root Cause

Two compounding issues in `dashboard/db.py`:

1. **Missing `role=` in SnowflakeURL** — without an explicit role, Snowflake falls back to the user's default role, which may not have `SELECT` on `PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS`.

2. **Unqualified table name** — `FCT_COMPANY_FINANCIALS` only resolves correctly if the Snowflake session's default schema is `MARTS`. Session schema resolution is fragile; the fully-qualified 3-part name is always unambiguous.

---

## The Fix

Two targeted changes in `dashboard/db.py`:

**1. Added `role=` to the Snowflake SQLAlchemy URL**

```python
role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),  # explicit role
```

`SNOWFLAKE_ROLE=PIPELINE_ROLE` was already being patched into the `snowflake-credentials` K8s secret in both namespaces by `scripts/deploy/sync.sh` (step 2c1a). The dashboard just wasn't reading it.

**2. Switched to a fully-qualified table name**

```python
_FINANCIALS_TABLE = "PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS" if DB_BACKEND == "snowflake" else "company_financials"
```

This mirrors how `FCT_ANOMALIES` is queried and removes all reliance on session default schema.

---

## Why This Fix

The anomaly detection query already used the correct pattern (fully-qualified name + PIPELINE_ROLE). Bringing financials into line with that pattern is consistent and removes two separate failure modes in one change. Neither fix requires any Snowflake-side changes — only the Python code and a redeploy.

---

## How to Verify After Redeploy

1. Run `./scripts/deploy.sh`
2. Open `http://localhost:32147/dashboard/`
3. Revenue and Net Income charts should render for AAPL / MSFT / GOOGL
4. Anomaly section should continue to work
