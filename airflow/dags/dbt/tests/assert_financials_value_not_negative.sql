-- Singular test: returns rows that represent failures — dbt passes the test only when zero rows are returned
-- Catches upstream data issues where a metric that should always be positive arrives as negative from EDGAR
-- Excludes metrics that can legitimately go negative (net losses, operating losses)
select
    ticker,
    metric,
    period_end,
    value
from {{ ref('fct_company_financials') }}
where value < 0
  and metric not in (
      'NetIncomeLoss',                                                                -- net loss years are valid negative values
      'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest',  -- pre-tax loss is valid
      'OperatingIncomeLoss',                                                          -- operating losses are valid
      'GrossProfit'                                                                   -- gross loss is valid in some edge cases
  )
