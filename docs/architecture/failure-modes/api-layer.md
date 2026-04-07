# Failure Modes — API Layer (SEC EDGAR / Open-Meteo)

Back to [Failure Mode Index](../FAILURE_MODE_MAP.md)

---

### API-1: Rate Limit / Quota Exceeded

| Field | Detail |
|-------|--------|
| **Symptoms** | SEC EDGAR: HTTP 403 if exceeding 10 requests/second. Open-Meteo: HTTP 429 if exceeding daily limit. |
| **Root cause** | SEC EDGAR allows 10 req/sec (no daily limit). Open-Meteo: 10,000 requests/day. `edgar_client.py` has a built-in `RateLimiter` class (token-bucket, 8 req/sec) that prevents hitting the SEC limit. |
| **Blast radius** | Extract task fails with HTTP error. No risk of garbage data passing downstream (unlike the old Alpha Vantage setup where rate-limit responses looked like HTTP 200 success). |
| **Prevention** | Rate limiting handled automatically by `RateLimiter` class in `edgar_client.py`. Response structure validated before returning (`facts` and `us-gaap` keys checked). |
| **Real incident?** | Alpha Vantage rate limits caused issues before migration to SEC EDGAR. No SEC EDGAR rate limit incidents since migration. |

### API-2: Schema Change

| Field | Detail |
|-------|--------|
| **Symptoms** | Transform task fails with KeyError or produces DataFrame with unexpected columns. Or succeeds but data is wrong. |
| **Root cause** | API provider changes JSON structure (renamed keys, nested differently, new required fields). Your XBRL concept names or response parsing assumptions break. |
| **Blast radius** | Silent data corruption if new schema partially overlaps old expectations. Loud failure if key paths completely change. |
| **Prevention** | Validate that expected top-level and nested keys exist before `json_normalize()`. Log schema fingerprint (sorted column list) and alert on change. |

### API-3: API Downtime / Timeout

| Field | Detail |
|-------|--------|
| **Symptoms** | Extract task hangs or raises `ConnectionError` / `Timeout`. |
| **Root cause** | External service is down, experiencing high latency, or network path is broken. |
| **Blast radius** | DAG run fails. If no retry configured, data is missing for that interval. |
| **Prevention** | Set explicit `timeout` on all `requests.get()` calls (e.g., 30 seconds). Configure Airflow task retries (2-3 retries with exponential backoff). |

### API-4: Empty / Malformed Response

| Field | Detail |
|-------|--------|
| **Symptoms** | API returns 200 OK but body is empty string, HTML error page, or truncated JSON. |
| **Root cause** | CDN/proxy error, partial response due to connection drop, or API returning error as HTML instead of JSON. |
| **Blast radius** | `json.loads()` fails (loud) or produces empty dict (silent). Downstream tasks get no data. |
| **Prevention** | Validate `Content-Type` header is `application/json`. Check `len(response.text) > minimum_threshold`. Wrap JSON parsing in try/except with informative error message. |

### API-5: Timezone / Date Boundary Issues

| Field | Detail |
|-------|--------|
| **Symptoms** | Requesting "today's" stock data returns empty or partial results. Works fine for historical dates. |
| **Root cause** | DAG scheduled in UTC but market operates in ET. Requesting data before market close (4 PM ET) returns incomplete daily data. Weekends and holidays return no data. |
| **Blast radius** | Missing data for current day. Load task may insert partial row or skip entirely. |
| **Prevention** | Schedule stock DAG after market close (e.g., 5 PM ET / 21:00 UTC). Handle empty results gracefully (log warning, skip insert, don't raise). |
