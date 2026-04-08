-- Company dimension table — one row per ticker, used for dashboard dropdowns and joins
-- Derived from fct_company_financials so it's always in sync with whatever tickers are in the fact table
-- tag:stocks — rebuilt every time dag_stocks.py triggers dbt
{{
    config(
        materialized='table',
        tags=['stocks']
    )
}}

-- DISTINCT handles the case where the same ticker appears across multiple metrics/years in the fact table
select distinct
    ticker,
    cik,
    entity_name
from {{ ref('fct_company_financials') }}  -- reads from PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS
