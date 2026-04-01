# ── Why this file still exists after the Alpha Vantage → SEC EDGAR migration ──
# This file used to contain Alpha Vantage API functions. Now it re-exports the
# EDGAR functions under the same module name so that dag_stocks.py can import
# from "stock_client" without changing its import path.
#
# All actual API logic lives in edgar_client.py (separation of concerns).
# This file is a thin re-export layer that keeps the DAG's imports clean.
# ─────────────────────────────────────────────────────────────────────────────

# Re-export EDGAR functions so dag_stocks.py can import from stock_client
from edgar_client import (
    resolve_cik,
    fetch_company_facts,
    flatten_company_financials,
    FINANCIAL_CONCEPTS,
)
