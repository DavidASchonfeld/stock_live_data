# Data Flow & Validation Gates

How data moves through the pipeline, what can go wrong at each stage, and what validation should catch problems before they propagate downstream.

**Navigation:**
- Want the full failure catalog? → [FAILURE_MODE_MAP.md](FAILURE_MODE_MAP.md)
- Need to understand component dependencies? → [COMPONENT_INTERACTIONS.md](COMPONENT_INTERACTIONS.md)
- System architecture overview? → [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md) (formerly ARCHITECTURE.md)

---

## Pipeline Overview

```
┌─────────┐    ┌───────────┐    ┌───────────┐    ┌─────────┐    ┌───────────┐
│  Stage 1 │───▶│  Stage 2  │───▶│  Stage 3  │───▶│ Stage 4 │───▶│  Stage 5  │
│ EXTRACT  │    │ TRANSFORM │    │   LOAD    │    │ STORAGE │    │  SERVE    │
│          │    │           │    │           │    │         │    │           │
│ API call │    │ Normalize │    │ Insert DB │    │ MariaDB │    │ Flask API │
│ raw JSON │    │ DataFrame │    │ to_sql()  │    │ tables  │    │ Dashboard │
└─────────┘    └───────────┘    └───────────┘    └─────────┘    └───────────┘
     │               │               │               │               │
   Gate 1          Gate 2          Gate 3          Gate 4          Gate 5
```

Each "gate" is a validation checkpoint. Data should not pass to the next stage unless it passes the gate. Currently, **most gates are missing or partial** — this document defines what each gate should check.

---

## Stage 1: Extract (API Ingestion)

**Location:** `scripts/stock_client.py`, `scripts/weather_client.py`
**What happens:** HTTP GET to external API → receive JSON response
**XCom transport:** Extract task returns data → Airflow serializes to JSON → stored in PostgreSQL

### What Can Fail

| Failure | Symptom | Current handling |
|---------|---------|-----------------|
| API timeout | `requests.ConnectionError` or `Timeout` | Unhandled — task crashes |
| HTTP error (4xx/5xx) | `response.status_code != 200` | Partially handled in stock DAG |
| Rate limit (Alpha Vantage) | HTTP 200, body contains `{"Note": "..."}` | Handled in stock DAG after fix |
| Empty response body | HTTP 200, `response.text == ""` | Not validated |
| HTML error page | HTTP 200, `Content-Type: text/html` | Not validated |
| Malformed JSON | `json.JSONDecodeError` | Not validated |
| Schema change | Valid JSON but keys renamed/restructured | Not validated |

### Gate 1: Extract Output Validation

What to check before returning data from the extract task:

```
Gate 1 Checklist:
├── HTTP status code == 200
├── Content-Type header contains "application/json"
├── Response body is non-empty (len > minimum threshold)
├── Response body parses as valid JSON
├── No rate-limit indicators:
│   ├── Alpha Vantage: no "Note" key, no "Information" key
│   └── Open-Meteo: no "reason" key with error message
├── Expected top-level keys exist:
│   ├── Stock: "Time Series (Daily)" or "Technical Analysis: SMA"
│   └── Weather: "hourly" containing "time" and "temperature_2m"
└── Data is non-trivial (e.g., time series has > 0 entries)
```

**On failure:** Raise an exception with a clear message identifying which check failed and including relevant response details (status code, first 200 chars of body). Do NOT return partial or error data downstream.

**Current state:** Stock DAG validates response structure and rate-limit messages (added 2026-03-31). Weather DAG needs the same treatment.

---

## Stage 2: Transform (Airflow DAG task)

**Location:** `airflow/dags/dag_stocks.py`, `airflow/dags/dag_weather.py`
**What happens:** Receive raw JSON from extract → `json_normalize()` → DataFrame with expected columns
**XCom transport:** Transform task receives data from XCom (deserialized from JSON) → outputs DataFrame as `to_dict(orient="records")`

### What Can Fail

| Failure | Symptom | Current handling |
|---------|---------|-----------------|
| XCom deserialization changes structure | Data shape different from extract output | Not validated |
| `json_normalize()` path wrong | KeyError or wrong columns | Unhandled — task crashes |
| Empty DataFrame after normalize | 0 rows, correct columns | Not validated |
| Wrong data types | Strings where floats expected | Not validated |
| All-null columns | Schema parsed but data missing | Not validated |
| Values out of range | Temperature = 9999, price = -1 | Not validated |

### Gate 2: Transform Output Validation

What to check before returning transformed data:

```
Gate 2 Checklist:
├── DataFrame is not empty (len > 0 rows)
├── Column names match expected schema exactly:
│   ├── Stock: {date, open, high, low, close, volume, sma_20, ...}
│   └── Weather: {time, temperature_2m, latitude, longitude, ...}
├── No unexpected extra columns (schema hasn't grown)
├── Data types are correct:
│   ├── Numeric columns are numeric (not strings)
│   ├── Date/time columns parse as valid dates
│   └── No columns that are all-null
├── Values are in plausible ranges:
│   ├── Stock prices > 0
│   ├── Temperature between -100 and 100 (Celsius)
│   └── Dates within expected range (not future dates, not 1970-01-01)
└── Row count is plausible (e.g., daily stock data should have 1-100 rows)
```

**On failure:** Raise with a schema diff — log expected columns vs. actual columns, expected types vs. actual types. This makes debugging API schema changes trivial.

**Current state:** No transform validation in either DAG.

---

## Stage 3: Load (Database Insert)

**Location:** Same DAG files, `load()` task
**What happens:** Receive DataFrame from XCom → `df.to_sql("table_name", con=engine, if_exists="append")`

### What Can Fail

| Failure | Symptom | Current handling |
|---------|---------|-----------------|
| Table doesn't exist (first run) | SQLAlchemy error on insert | Weather DAG: table pre-created manually |
| Column type mismatch | Insert fails or silently truncates | Not validated |
| Duplicate rows | Same date inserted twice | Not validated (no unique constraint) |
| Connection lost mid-insert | Partial insert (no transaction) | Not validated |
| DB credentials missing | `Access Denied` error | Validated at import time (`_required_secrets`) |
| DataFrame has wrong columns | Insert fails or creates wrong columns | Not validated |
| Silent zero-row insert | `to_sql()` succeeds but DataFrame was empty | Not validated |

### Gate 3: Load Validation

What to check before and after inserting:

```
Gate 3 Checklist (PRE-INSERT):
├── DataFrame columns match DB table schema
├── DataFrame has > 0 rows
├── No duplicate rows for dates already in DB:
│   ├── Query: SELECT MAX(date) FROM table
│   └── Filter DataFrame to only rows after max date
└── DB connection is alive (test query: SELECT 1)

Gate 3 Checklist (POST-INSERT):
├── to_sql() returned without error
├── Row count in DB increased by expected amount:
│   ├── Count before: SELECT COUNT(*) FROM table
│   ├── Count after: SELECT COUNT(*) FROM table
│   └── Difference matches DataFrame row count
└── Spot-check: latest inserted row has plausible values
```

**On failure:** Log row count before/after, log first failing row. Use a transaction so partial inserts roll back cleanly.

**Current state:** Pre-insert: credential validation only. Post-insert: no validation.

---

## Stage 4: Storage (MariaDB)

**Location:** MariaDB pod (K8s), database `database_one`
**What happens:** Data at rest in `stock_daily_prices` and `weather_hourly` tables

### What Can Fail

| Failure | Symptom | Current handling |
|---------|---------|-----------------|
| Disk full | INSERT fails | Not monitored |
| Table schema drift | Manual ALTER TABLE not tracked in code | Not validated |
| Data corruption (silent garbage) | Wrong values from failed validation upstream | Not detected |
| Connection pool exhaustion | Too many concurrent connections | Not limited |
| No data freshness guarantee | Stale data with no indication | Not monitored |

### Gate 4: Storage Health Validation

What to check periodically (not per-request):

```
Gate 4 Checklist (PERIODIC):
├── Tables exist with expected schema
│   ├── DESCRIBE stock_daily_prices → expected columns
│   └── DESCRIBE weather_hourly → expected columns
├── Data is fresh (most recent row within expected interval):
│   ├── Stock: latest date within 1-2 business days
│   └── Weather: latest time within 2 hours
├── Row counts are growing (not stuck):
│   ├── Compare today's count to yesterday's
│   └── Alert if delta is 0 for 2+ days
├── No obvious corruption:
│   ├── No NULL values in required columns
│   ├── No duplicate dates
│   └── Numeric values in plausible ranges
└── Disk usage < 80% on MariaDB PV
```

**Current state:** `validate_database.py` exists in `airflow/dags/` — verify its current checks and extend.

---

## Stage 5: Serve (Flask API → Dashboard)

**Location:** `dashboard/` directory, Flask app
**What happens:** Flask reads from MariaDB → serves JSON to Dash frontend

### What Can Fail

| Failure | Symptom | Current handling |
|---------|---------|-----------------|
| DB unreachable | Flask returns 500 | Unhandled — raw error shown |
| Empty result set | API returns `[]`, dashboard shows nothing | Not distinguished from "no data yet" |
| Stale data | Old data served with no warning | No freshness indicator |
| Query error after schema change | SQL SELECT fails on renamed column | Not handled |

### Gate 5: Serving Validation

What to check before returning data to the dashboard:

```
Gate 5 Checklist:
├── DB query returned > 0 rows
│   └── If 0 rows: return {"status": "no_data", "message": "Awaiting first data load"}
├── Data is fresh:
│   ├── Include "last_updated" timestamp in response
│   ├── Include "data_age_hours" field
│   └── If data older than threshold: include "stale": true flag
├── Response is well-formed:
│   └── JSON serializable, no NaN/Infinity (these break JSON)
└── Response size is reasonable (detect truncation or runaway queries)
```

**Current state:** No serving validation. Flask returns raw query results.

---

## XCom: The Hidden Transport Layer

Data between Airflow tasks moves through XCom (Cross-Communication). This is invisible but introduces its own failure modes.

```
Task A (extract) → returns Python object
  ↓
Airflow serializes to JSON → stores in PostgreSQL (xcom table)
  ↓
Task B (transform) → receives deserialized Python object
  ↓
Airflow serializes to JSON → stores in PostgreSQL (xcom table)
  ↓
Task C (load) → receives deserialized Python object
```

### XCom Failure Modes

1. **Serialization changes structure** — `datetime` objects become strings. `numpy` types become basic Python types. `NaN` may become `null`. If your code depends on specific types, the round-trip breaks it.

2. **Large data overflow** — XCom stores data in PostgreSQL. Large DataFrames (>10MB serialized) can cause PostgreSQL performance issues or exceed `max_allowed_packet`. For this project's data volumes (daily stock/weather data), this isn't a concern yet.

3. **Non-serializable objects** — If a task returns an object that can't be JSON-serialized, the task "succeeds" (the code ran) but XCom storage fails. The downstream task gets nothing.

### XCom Best Practices for This Project

- Always return `df.to_dict(orient="records")` from transform tasks (produces clean JSON-serializable list of dicts)
- In the receiving task, validate the structure immediately: `assert isinstance(data, list) and len(data) > 0`
- Log `type(data)` and `len(data)` at the start of each task that receives XCom data
- If data grows large, consider writing to a temp file on the shared PV instead of passing through XCom

---

## Validation Implementation Priority

Based on risk (likelihood x impact) and current gaps:

| Priority | Gate | What to add | Risk if skipped |
|----------|------|-------------|-----------------|
| 1 | Gate 1 (Extract) | Response validation for Weather DAG | Silent garbage data or rate-limit message passed downstream |
| 2 | Gate 2 (Transform) | Schema check (expected columns, types) | Data corruption in DB from schema changes |
| 3 | Gate 3 (Load) | Duplicate detection + post-insert row count | Duplicate data or silent zero inserts |
| 4 | Gate 5 (Serve) | Freshness indicator in API response | Users see stale data without knowing it |
| 5 | Gate 4 (Storage) | Periodic health check (extend validate_database.py) | Slow-burn issues undetected |

---

**Last updated:** 2026-03-31
