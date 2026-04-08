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
        -- keep most recently imported row if same observation_time + location appears more than once
        row_number() over (
            partition by observation_time, latitude, longitude
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
