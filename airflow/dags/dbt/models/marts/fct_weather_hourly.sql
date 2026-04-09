-- Fact table for Open-Meteo hourly weather — dashboard-ready, deduplicated
-- RAW already deduplicates on insert in dag_weather.py, but this adds a safety layer in case of race conditions
-- tag:weather — dag_weather.py runs `dbt run --select tag:weather` after each Snowflake write
{{
    config(
        materialized='table',
        tags=['weather']
    )
}}

with deduplicated as (
    select
        *,
        -- single-location pipeline: observation_time is the primary key; partition by it alone
        -- so the unique test in schema.yml holds even if Open-Meteo snaps coordinates differently across calls
        row_number() over (
            partition by observation_time
            order by imported_at desc nulls last
        ) as rn
    from {{ ref('stg_weather_hourly') }}  -- reads from PIPELINE_DB.STAGING.STG_WEATHER_HOURLY view
)

select
    observation_time,
    temperature_f,
    latitude,
    longitude,
    elevation,
    timezone,
    utc_offset_seconds,
    imported_at
from deduplicated
where rn = 1  -- drop duplicate rows
