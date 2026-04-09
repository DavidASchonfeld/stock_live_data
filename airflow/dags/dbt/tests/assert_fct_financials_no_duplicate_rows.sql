{{ config(tags=['stocks']) }}  -- tag:stocks so `dbt test --select tag:stocks` in dag_stocks.py includes this test
-- Singular test: returns rows that represent failures — dbt passes the test only when zero rows are returned
-- Catches dedup failures: (ticker, metric, period_end) must be unique after ROW_NUMBER() in fct_company_financials
select
    ticker,
    metric,
    period_end,
    count(*) as cnt
from {{ ref('fct_company_financials') }}
group by ticker, metric, period_end
having count(*) > 1  -- any group > 1 means the dedup window function failed to produce a unique row
