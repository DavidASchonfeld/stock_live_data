# Airflow DAG Import Timeout

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### AF-1: DagBag Parse Timeout — weather_consumer_pipeline (Apr 11 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | `DagBag import timeout for dag_weather_consumer.py after 30.0s` — DAG not found at startup, tasks flipped to UP_FOR_RESCHEDULE |
| **Cause** | `import pandas as pd` and `from sqlalchemy.exc import SQLAlchemyError` at module level. Airflow evaluates all module-level code on every DagBag scan. Pandas' import chain is heavy; under scheduler CPU pressure it exceeded the 30s timeout. |
| **Fix** | Moved both imports inside `write_to_snowflake()` (the only task that uses them). Applied same fix to `dag_stocks_consumer.py` which had the identical pattern. Matches the existing deferred-import convention used for `KafkaConsumer`, `SnowflakeHook`, and `write_df_to_snowflake`. |
| **Rule** | Never put heavy libraries (`pandas`, `sqlalchemy`, `sklearn`, etc.) at DAG module level — defer all non-Airflow imports to inside `@task` functions. |
| **Real incident?** | Yes — Apr 11 2026. |

---

### AF-2: DagBag Parse Timeout — Stock_Market_Pipeline / Weather_Pipeline (Apr 12 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | `DagBag import timeout for dag_stocks.py after 30.0s` — DAG not found at startup, tasks flipped to UP_FOR_RESCHEDULE. `dag_weather.py` had the identical latent issue. |
| **Cause** | `import pandas as pd` at module level in both producer DAGs (`dag_stocks.py` line 12, `dag_weather.py` line 12). Pandas initialises PyArrow and heavy C extensions on import, which exceeded the 30s DagBag parse timeout on the constrained EC2 node. Consumer DAGs were already correct — they defer pandas inside task functions. |
| **Fix** | Removed top-level `import pandas as pd` from both files. Added `import pandas as pd` as the first line inside `transform()` in each file — the only task that uses it. |
| **Rule** | All four DAGs now defer every heavy import to inside the `@task` function where it is needed. Never import pandas, numpy, scikit-learn, etc. at DAG module level. |
| **Real incident?** | Yes — Apr 12 2026. |

---

### AF-3: DagBag Parse Timeout — API_Weather-Pull_Data via weather_client.py (Apr 12 2026)

| Field | Detail |
|-------|--------|
| **Symptom** | `DagBag import timeout for dag_weather.py after 30.0s` — DAG not found at startup, `extract` task flipped to UP_FOR_RESCHEDULE |
| **Cause** | `import pandas as pd` at module level in `weather_client.py` (line 6). `dag_weather.py` imports `weather_client` at module level, so Airflow triggers the full pandas init chain on every DagBag scan — indirectly, through a helper module. The AF-2 fix moved pandas out of the DAG files themselves but missed this transitive import. |
| **Fix** | Removed `import pandas as pd` from the top of `weather_client.py`. Moved it to the first line inside the `if __name__ == "__main__":` block, where it was only ever used. `fetch_weather_forecast()` itself does not use pandas at all. |
| **Rule** | Helper modules imported at DAG module level must also be free of heavy top-level imports. The no-heavy-imports-at-module-level rule applies transitively to any file reachable from a DAG's top-level import chain. |
| **Stocks DAG audit** | Confirmed clean — `dag_stocks.py`, `dag_stocks_consumer.py`, `edgar_client.py` have no heavy top-level imports. `snowflake_client.py` has pandas at its module level but is only ever imported inside task functions (deferred), so it is not affected. |
| **Real incident?** | Yes — Apr 12 2026. |
