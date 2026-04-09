# Kafka Topic Naming Convention

## Rule
Use hyphens (`-`) as separators in topic names. Never use periods (`.`) or underscores (`_`).

**Examples:**
- `stocks-financials-raw` ✓
- `weather-hourly-raw` ✓
- `stocks.financials.raw` ✗
- `weather_hourly_raw` ✗

---

## The Warning

When topics use `.` or `_`, Kafka prints:

```
WARNING: Due to limitations in metric names, topics with a period ('.') or underscore ('_')
could collide. To avoid issues it is best to use either, but not both.
```

## Why It Happens

Kafka exposes internal metrics via JMX (and Prometheus). When building metric names, Kafka
converts **both** `.` and `_` to `_`. This means two topics like `stocks.financials.raw` and
`stocks_financials_raw` would produce the **same metric name** — a silent collision.

Kafka detects that you're using one of the dangerous characters and warns you proactively,
even if no collision exists yet.

## Why Hyphens Fix It

Hyphens (`-`) are **not** converted in metric names — they pass through as-is. A topic named
`stocks-financials-raw` produces the metric name `stocks-financials-raw`, with no ambiguity
and no risk of colliding with any other topic name. The warning disappears entirely.

---

## Fix Applied (2026-04-09)

Renamed both topics from dot-separated to hyphen-separated:

| Old name | New name |
|---|---|
| `stocks.financials.raw` | `stocks-financials-raw` |
| `weather.hourly.raw` | `weather-hourly-raw` |

**Files updated:**
- `scripts/deploy.sh` — topic creation commands
- `airflow/dags/dag_stocks.py` — producer `producer.send()`
- `airflow/dags/dag_stocks_consumer.py` — consumer `KafkaConsumer()`
- `airflow/dags/dag_weather.py` — producer `producer.send()`
- `airflow/dags/dag_weather_consumer.py` — consumer `KafkaConsumer()`

Old topics were deleted from the live cluster with `kafka-topics.sh --delete`.
New topics are created on the next `./scripts/deploy.sh` run via `--if-not-exists`.
