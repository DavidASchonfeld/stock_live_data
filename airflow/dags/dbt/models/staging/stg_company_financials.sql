-- Staging view for SEC EDGAR financials — renames nothing (RAW columns are already clean), casts types
-- tag:stocks targets this model when dag_stocks.py runs `dbt run --select tag:stocks`
{{
    config(
        materialized='view',
        tags=['stocks']
    )
}}

select
    ticker,
    cik,
    entity_name,
    metric,
    label,
    try_to_date(period_end)  as period_end,   -- safe cast: returns NULL on bad values instead of erroring
    try_cast(value as float) as value,         -- RAW stores value as variant/string from JSON — cast to FLOAT
    try_to_date(filed_date)  as filed_date,
    form_type,
    fiscal_year,
    fiscal_period,
    frame
from {{ source('raw', 'COMPANY_FINANCIALS') }}  -- resolves to PIPELINE_DB.RAW.COMPANY_FINANCIALS
where ticker is not null
  and metric is not null  -- drop malformed rows before they reach marts
