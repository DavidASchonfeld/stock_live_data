-- Staging view for Open-Meteo weather — converts epoch integer columns to TIMESTAMP_NTZ for readability
-- tag:weather targets this model when dag_weather.py runs `dbt run --select tag:weather`
{{
    config(
        materialized='view',
        tags=['weather']
    )
}}

select
    to_timestamp(time)        as observation_time,  -- TIME is stored as epoch seconds (NUMBER) in RAW — convert to TIMESTAMP_NTZ
    temperature_2m            as temperature_f,      -- already in Fahrenheit (fahrenheit=True set in dag_weather.py extract)
    latitude,
    longitude,
    elevation,
    timezone,
    utc_offset_seconds,
    to_timestamp(imported_at) as imported_at         -- imported_at also stored as epoch seconds by snowflake_client.py
from {{ source('raw', 'WEATHER_HOURLY') }}  -- resolves to PIPELINE_DB.RAW.WEATHER_HOURLY
where time is not null  -- guard against partially written rows
