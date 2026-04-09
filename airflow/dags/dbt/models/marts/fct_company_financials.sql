-- Fact table for annual SEC EDGAR financials — this is what the dashboard queries via PIPELINE_DB.MARTS
-- Deduplicates in case the same ticker/metric/period appears in multiple XBRL frames
-- tag:stocks — dag_stocks.py runs `dbt run --select tag:stocks` after writing to RAW
{{
    config(
        materialized='table',
        tags=['stocks']
    )
}}

with deduplicated as (
    select
        *,
        -- keep the most recently filed row; frame asc breaks ties deterministically when filed_date is the same date
        row_number() over (
            partition by ticker, metric, period_end, fiscal_period
            order by filed_date desc nulls last, frame asc nulls last
        ) as rn
    from {{ ref('stg_company_financials') }}  -- reads from PIPELINE_DB.STAGING.STG_COMPANY_FINANCIALS view
    where fiscal_period = 'FY'  -- annual filings only — matches annual_only=True in dag_stocks.py
)

-- column names intentionally match dashboard/db.py _load_ticker_data() query: metric, label, period_end, value, fiscal_year, fiscal_period
select
    ticker,
    cik,
    entity_name,
    metric,
    label,
    period_end,
    value,
    filed_date,
    form_type,
    fiscal_year,
    fiscal_period,
    frame
from deduplicated
where rn = 1  -- drop duplicate rows, keep only the keeper selected by the window function above
