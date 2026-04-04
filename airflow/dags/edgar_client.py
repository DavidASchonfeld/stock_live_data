# Python Libraries
import json
import os
import time
import threading
from typing import Any

import requests


# ── Why this file exists (separation of concerns) ────────────────────────────
# The DAG file (dag_stocks.py) only orchestrates the pipeline steps. All the
# details of HOW to talk to SEC EDGAR — URL construction, HTTP call, rate
# limiting, response parsing — live here instead. This makes the DAG easier
# to read and makes the API logic independently testable.
#
# Three functions, each a separate concern:
#   resolve_cik()                →  ticker symbol → 10-digit CIK string
#   fetch_company_facts()        →  raw JSON from SEC EDGAR XBRL API
#   flatten_company_financials() →  clean list of row-dicts ready for SQL
# ─────────────────────────────────────────────────────────────────────────────

# ── SEC EDGAR API info ───────────────────────────────────────────────────────
# No API key required — SEC EDGAR is free U.S. government public domain data
# Rate limit: 10 requests/second (SEC policy, not a technical block)
# Required: User-Agent header with contact info (SEC will block without it)
# Docs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
# ─────────────────────────────────────────────────────────────────────────────

# Contact email for SEC User-Agent — loaded from env so it stays out of git history
EDGAR_CONTACT_EMAIL = os.environ.get("EDGAR_CONTACT_EMAIL", "contact@stocklivedata.dev")

# SEC requires a descriptive User-Agent so they can contact you if your script misbehaves
EDGAR_USER_AGENT = f"DataPipeline Portfolio Project {EDGAR_CONTACT_EMAIL}"

# Base URLs for the two SEC EDGAR endpoints we use
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

# ── Financial concepts to extract from XBRL data ────────────────────────────
# Each tuple: (XBRL concept name, human-readable label, unit key in response)
# These are standard US-GAAP tags — same ones used in 10-K and 10-Q filings
FINANCIAL_CONCEPTS: list[tuple[str, str, str]] = [
    ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenue", "USD"),
    ("NetIncomeLoss", "Net Income", "USD"),
    ("EarningsPerShareDiluted", "EPS (Diluted)", "USD/shares"),
    ("Assets", "Total Assets", "USD"),
    ("Liabilities", "Total Liabilities", "USD"),
    ("StockholdersEquity", "Stockholders Equity", "USD"),
    ("OperatingIncomeLoss", "Operating Income", "USD"),
    ("GrossProfit", "Gross Profit", "USD"),
    ("CashAndCashEquivalentsAtCarryingValue", "Cash and Equivalents", "USD"),
    ("ResearchAndDevelopmentExpense", "R&D Expense", "USD"),
]


# ── Rate Limiter ─────────────────────────────────────────────────────────────
# SEC EDGAR allows 10 requests/second. Even though our pipeline only makes a
# handful of calls, building a proper rate limiter is best practice and shows
# recruiters you think about API etiquette and production-readiness.
#
# This is a token-bucket rate limiter: it allows bursts up to max_requests,
# then enforces a minimum delay between subsequent calls.
# ─────────────────────────────────────────────────────────────────────────────
class RateLimiter:
    """Token-bucket rate limiter — controls how fast we hit external APIs."""

    def __init__(self, max_requests_per_second: float = 8.0):
        # Stay under SEC's 10/sec limit with a safety margin of 2 req/sec
        self.min_interval: float = 1.0 / max_requests_per_second
        # Tracks when we last made a request so we know how long to wait
        self._last_request_time: float = 0.0
        # Thread lock prevents race conditions if multiple tasks share this limiter
        self._lock: threading.Lock = threading.Lock()

    def wait(self) -> None:
        """Block until enough time has passed since the last request."""
        with self._lock:
            now = time.monotonic()
            # Calculate how long we need to sleep before the next request is allowed
            elapsed = now - self._last_request_time
            if elapsed < self.min_interval:
                # Sleep just long enough to stay under the rate limit
                time.sleep(self.min_interval - elapsed)
            # Record this request's timestamp for the next call's calculation
            self._last_request_time = time.monotonic()


# Module-level rate limiter — shared across all functions in this file
_rate_limiter = RateLimiter(max_requests_per_second=8.0)


def _get_with_rate_limit(url: str) -> requests.Response:
    """Make a GET request to SEC EDGAR, respecting rate limits and required headers."""
    # Pause if we're sending requests too fast (SEC policy: max 10/sec)
    _rate_limiter.wait()

    # SEC blocks requests without a User-Agent identifying the caller
    headers = {"User-Agent": EDGAR_USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=30)
        # Raise an exception for 4xx/5xx HTTP status codes
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        print(f"SEC EDGAR HTTP error: {response.status_code} for {url}")
        raise
    except Exception as error:
        print(f"SEC EDGAR request failed: {error}")
        raise

    return response


# ── CIK cache ────────────────────────────────────────────────────────────────
# company_tickers.json is ~2MB and rarely changes. We fetch it once per DAG
# run and cache it in memory so we don't re-download it for every ticker.
# ─────────────────────────────────────────────────────────────────────────────
_cik_cache: dict[str, str] | None = None


def resolve_cik(ticker: str) -> str:
    """
    Convert a ticker symbol (e.g. 'AAPL') to a 10-digit zero-padded CIK string.

    SEC EDGAR identifies companies by CIK, not ticker. This function downloads
    the official SEC ticker→CIK mapping and caches it for the duration of the
    DAG run so subsequent lookups are instant.
    """
    global _cik_cache

    if _cik_cache is None:
        # Fetch the official SEC ticker-to-CIK mapping (one HTTP call, cached after)
        response = _get_with_rate_limit(EDGAR_TICKERS_URL)
        raw_mapping: dict = json.loads(response.content)
        # Build a fast lookup dict: {"AAPL": "0000320193", "MSFT": "0000789019", ...}
        _cik_cache = {
            entry["ticker"]: str(entry["cik_str"]).zfill(10)
            for entry in raw_mapping.values()
        }

    # Convert ticker to uppercase for case-insensitive matching
    ticker_upper = ticker.upper()

    if ticker_upper not in _cik_cache:
        raise ValueError(f"Ticker '{ticker}' not found in SEC EDGAR company_tickers.json")

    return _cik_cache[ticker_upper]


def fetch_company_facts(cik: str) -> dict[str, Any]:
    """
    Fetch all XBRL financial data for a company from SEC EDGAR.

    Parameters
    ----------
    cik : 10-digit zero-padded CIK string (from resolve_cik())

    Returns
    -------
    Raw JSON response as a dict. Top-level shape:
        {
          "cik": 320193,
          "entityName": "Apple Inc.",
          "facts": {
              "us-gaap": {
                  "NetIncomeLoss": { "units": { "USD": [...] }, ... },
                  ...
              }
          }
        }
    """
    # Build the URL with zero-padded CIK (SEC requires exactly 10 digits)
    url = EDGAR_COMPANY_FACTS_URL.format(cik=cik)

    response = _get_with_rate_limit(url)
    data: dict = json.loads(response.content)

    # Validate that the response contains the expected XBRL structure
    if "facts" not in data:
        raise ValueError(f"Unexpected response shape for CIK {cik}: missing 'facts' key")

    if "us-gaap" not in data["facts"]:
        raise ValueError(f"No US-GAAP data found for CIK {cik} — company may use IFRS or be non-US")

    return data


def flatten_company_financials(
    ticker: str,
    raw_response: dict[str, Any],
    annual_only: bool = True,
) -> list[dict[str, Any]]:
    """
    Flatten nested XBRL companyfacts JSON into a list of row-dicts for SQL.

    Each dict in the returned list maps to one financial metric for one period
    and is safe to pass directly into pd.DataFrame() or Snowflake's write_pandas().

    Parameters
    ----------
    ticker       : Original ticker symbol (e.g. "AAPL") — stored alongside CIK for readability
    raw_response : Raw JSON from fetch_company_facts()
    annual_only  : If True, only keep 10-K annual filings (skip quarterly 10-Q)

    Returns
    -------
    List of dicts, one per metric per period:
        [{ "ticker", "cik", "entity_name", "metric", "label", "period_end",
           "value", "filed_date", "form_type", "fiscal_year", "fiscal_period",
           "frame" }, ...]
    """
    # Extract top-level metadata from SEC response
    cik = str(raw_response["cik"])
    entity_name = raw_response.get("entityName", "Unknown")
    gaap_facts = raw_response["facts"]["us-gaap"]

    records: list[dict[str, Any]] = []

    for xbrl_concept, human_label, expected_unit in FINANCIAL_CONCEPTS:
        # Not every company reports every concept — skip gracefully if missing
        if xbrl_concept not in gaap_facts:
            continue

        concept_data = gaap_facts[xbrl_concept]
        units = concept_data.get("units", {})

        # Find the matching unit key (e.g. "USD" or "USD/shares")
        if expected_unit not in units:
            continue

        for entry in units[expected_unit]:
            # Filter to 10-K (annual) filings only — cleaner dataset, less noise
            if annual_only and entry.get("form") != "10-K":
                continue

            records.append({
                "ticker": ticker,
                "cik": cik,
                "entity_name": entity_name,
                "metric": xbrl_concept,
                # Human-readable label (e.g. "Revenue") for dashboards and reports
                "label": human_label,
                # "end" is the period end date (e.g. end of fiscal year)
                "period_end": entry.get("end", ""),
                # "val" is the actual dollar/share amount from the filing
                "value": entry.get("val"),
                # "filed" is when the company submitted the filing to the SEC
                "filed_date": entry.get("filed", ""),
                # "form" distinguishes 10-K (annual) from 10-Q (quarterly)
                "form_type": entry.get("form", ""),
                # Fiscal year and period help align data across companies
                "fiscal_year": entry.get("fy"),
                "fiscal_period": entry.get("fp", ""),
                # "frame" is SEC's calendar alignment tag (e.g. "CY2023")
                "frame": entry.get("frame", ""),
            })

    return records


# ── Only runs if this script is called directly, not when imported ───────────
if __name__ == "__main__":
    TEST_TICKER = "AAPL"

    print(f"Resolving CIK for {TEST_TICKER}...")
    cik = resolve_cik(TEST_TICKER)
    print(f"  CIK: {cik}")

    print(f"Fetching company facts...")
    raw = fetch_company_facts(cik)
    print(f"  Entity: {raw.get('entityName')}")

    print(f"Flattening financials...")
    rows = flatten_company_financials(TEST_TICKER, raw)
    print(f"  {len(rows)} rows extracted")

    if rows:
        print(f"  First row: {json.dumps(rows[0], indent=2)}")
