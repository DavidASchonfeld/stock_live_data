# Python Libraries
import json
from typing import Any

import requests
from urllib.parse import urlencode


# ── Why this file exists (separation of concerns) ────────────────────────────
# The DAG file (dag_stocks.py) only orchestrates the pipeline steps. All the
# details of HOW to talk to Alpha Vantage — URL construction, HTTP call,
# error handling, response parsing — live here instead. This makes the DAG
# easier to read and makes the API logic independently testable.
#
# Two functions, not one, because fetching and flattening are separate concerns:
#   sendRequest_alphavantage_daily()  →  raw JSON from the API
#   flatten_daily_timeseries()        →  clean list of row-dicts ready for SQL
# Keeping them apart makes it easy to unit-test the flatten logic with a
# fixture dict, without making a real HTTP call.
# ─────────────────────────────────────────────────────────────────────────────

# ── Alpha Vantage free-tier limits ──────────────────────────────────────────
# 25 API calls / day  (free tier)
# Endpoints used: TIME_SERIES_DAILY
# Docs: https://www.alphavantage.co/documentation/
# ────────────────────────────────────────────────────────────────────────────

ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"


def sendRequest_alphavantage_daily(symbol: str, api_key: str, outputsize: str = "compact") -> dict[str, Any]:
    """
    ### Fetch daily OHLCV stock data from Alpha Vantage.

    Parameters
    ----------
    symbol     : Ticker symbol, e.g. "AAPL"
    api_key    : Your Alpha Vantage API key (stored in api_key.py)
    outputsize : "compact" = last 100 trading days  |  "full" = 20+ years of history

    Returns
    -------
    Raw JSON response as a dict.  Shape:
        {
          "Meta Data": { "2. Symbol": "AAPL", ... },
          "Time Series (Daily)": {
              "2024-01-02": { "1. open": ..., "2. high": ..., "3. low": ...,
                              "4. close": ..., "5. volume": ... },
              ...
          }
        }
    """
    params = {
        "function"   : "TIME_SERIES_DAILY",
        "symbol"     : symbol,
        "outputsize" : outputsize,  # "compact" keeps us under the 25-call/day free limit
        "apikey"     : api_key,
    }

    try:
        response: requests.Response = requests.get(ALPHA_VANTAGE_BASE_URL, params=params)
        response.raise_for_status()
    except requests.exceptions.HTTPError:
        print("response.status_code: " + str(response.status_code))
        raise requests.exceptions.HTTPError("Response Status Code: " + str(response.status_code))
    except Exception as error:
        print("Request failed: " + str(error))
        raise

    response_dict: dict = json.loads(response.content)

    # Alpha Vantage returns a plain dict with an "Information" key when rate-limited
    if "Information" in response_dict:
        raise RuntimeError("Alpha Vantage rate limit hit: " + response_dict["Information"])

    # Validate that the expected key exists before returning
    if "Time Series (Daily)" not in response_dict:
        raise ValueError(f"Unexpected response shape for {symbol}: " + str(response_dict.keys()))

    return response_dict


def flatten_daily_timeseries(symbol: str, raw_response: dict[str, Any]) -> list[dict[str, Any]]:
    """
    ### Flatten the nested Alpha Vantage response into a list of row-dicts.

    Each dict in the returned list maps to one trading day and is safe to
    pass directly into pd.DataFrame() or Snowflake's write_pandas().

    Returns
    -------
    List of dicts, one per trading day:
        [{ "ticker", "date", "open", "high", "low", "close", "volume" }, ...]
    """
    time_series: dict = raw_response.get("Time Series (Daily)", {})

    # Rename Alpha Vantage's numbered keys ("1. open") to clean column names
    records = [
        {
            "ticker" : symbol,
            "date"   : date_str,
            "open"   : float(values["1. open"]),
            "high"   : float(values["2. high"]),
            "low"    : float(values["3. low"]),
            "close"  : float(values["4. close"]),
            "volume" : int(values["5. volume"]),
        }
        for date_str, values in time_series.items()
    ]

    return records


# ── Only runs if this script is called directly, not when imported ───────────
if __name__ == "__main__":
    # Quick smoke-test — replace with your actual key or import from api_key.py
    from api_key import api_keys  # type: ignore

    TEST_SYMBOL = "AAPL"
    raw = sendRequest_alphavantage_daily(TEST_SYMBOL, api_keys.alpha_vantage["key"])
    rows = flatten_daily_timeseries(TEST_SYMBOL, raw)

    print(f"Fetched {len(rows)} rows for {TEST_SYMBOL}")
    print(rows[0])  # print first row as a sanity check
