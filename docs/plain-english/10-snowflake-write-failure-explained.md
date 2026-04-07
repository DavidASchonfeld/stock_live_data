# Part 10: The Snowflake Weather Write Failure - What Went Wrong and How We Fixed It

> Part of the [Plain English Guide](README.md)  
> Date: April 7, 2026

## The Problem (In Simple Terms)

Your weather pipeline was **successfully fetching data from the Open-Meteo API** (168 rows every hour) and **successfully writing it to MariaDB** (the local database). But when it tried to write the same data to Snowflake (the cloud data warehouse), it would **fail silently** — the data never appeared in Snowflake's tables.

**What we saw:**
- ✅ API call: 168 rows extracted
- ✅ MariaDB write: 168 rows inserted
- ❌ Snowflake write: 0 rows inserted (silently failed)

**Why this mattered:** You need to populate your Snowflake tables to be able to analyze the data later using dbt and other tools.

---

## The Root Cause: A Timestamp Mismatch

This is where it gets tricky. To understand the problem, you need to know how timestamps are stored in Snowflake.

### What Happened in the Past (The Setup)

Earlier, you created the Snowflake table with the correct column types:
```sql
CREATE TABLE PIPELINE_DB.RAW.WEATHER_HOURLY (
  TIME TIMESTAMP_NTZ,        ← "timestamp not timezone-aware"
  TEMPERATURE_2M FLOAT,
  TIMEZONE VARCHAR(50),
  ...
)
```

**TIMESTAMP_NTZ means:** A date and time, like "2026-04-07 14:30:00"

### The Problem: The Table Schema Got Broken

But when the Python code first tried to auto-create the table (using `write_pandas()` from Snowflake), it looked at the data types in your Pandas DataFrame and **incorrectly inferred the types**. Instead of creating a TIMESTAMP column, it created a NUMBER column:

```sql
-- What should have been created:
TIME TIMESTAMP_NTZ,

-- What actually got created:
TIME NUMBER(38,0),        ← Stores numbers like 1712476800
```

**NUMBER(38,0)** is Snowflake's way of storing really big integers. When you store a timestamp as a NUMBER, you're storing it as **Unix epoch seconds** (the number of seconds since January 1, 1970).

### Why This Caused the Write to Fail: The Deduplication Logic

The weather pipeline has a smart feature: before writing to Snowflake, it checks **"Have I already written this data?"** to prevent duplicates. The code did this by:

1. **Querying Snowflake for existing times:**
   ```python
   cur.execute("SELECT TIME FROM PIPELINE_DB.RAW.WEATHER_HOURLY")
   # Returns: [1712476800, 1712480400, 1712484000, ...]  ← Unix epoch numbers!
   ```

2. **Converting them to strings for comparison:**
   ```python
   sf_existing = [str(row[0]) for row in sf_cur.fetchall()]
   # Result: ["1712476800", "1712480400", "1712484000", ...]
   ```

3. **Getting the times from the API and converting to strings:**
   ```python
   df_times_str = df["time"].astype(str)
   # Result: ["2026-04-07T00:00", "2026-04-07T01:00", "2026-04-07T02:00", ...]
   # These are ISO format (human-readable) strings!
   ```

4. **Comparing them:**
   ```python
   sf_new_rows = df[~df_times_str.isin(sf_existing)]
   # Comparing: "2026-04-07T00:00" vs "1712476800"
   # Result: They never match!
   ```

**The Problem:** We were comparing **human-readable date strings** ("2026-04-07T00:00") with **Unix epoch numbers as strings** ("1712476800"). They're completely different formats, so the comparison always failed.

**The Impact:** The code thought **every row was new** (because they never matched), so it tried to insert them all. But then the INSERT statement itself would fail because the data types didn't match.

---

## The Timestamp Conversion Problem

There was a second issue in the code that inserts data. The `snowflake_client.py` file has logic to convert Python datetime objects to the format Snowflake expects:

```python
# Original code (wrong order):
elif isinstance(val, str):
    # Handle strings
    values.append(val)
elif isinstance(val, (datetime, pd.Timestamp)):
    # Convert datetime to epoch seconds
    epoch_seconds = int(val.timestamp())
    values.append(str(epoch_seconds))
```

**The problem:** The `str` check came FIRST. So when a datetime object arrived, Python would try to convert it to a string first, and the datetime conversion code would never run.

**This is like a security guard checking "Are you a visitor?" before asking "Are you an employee?" — if employees answer "Yes, I'm a person," they get treated as visitors.**

---

## The Fix (What We Changed)

### Fix #1: Convert Timestamps to Epoch Seconds Before Comparing

We changed the deduplication logic to **convert API timestamps to Unix epoch format FIRST**, so we're comparing the same types:

```python
# New code (correct):
# 1. Get existing times from Snowflake (they're epoch numbers)
sf_existing = {int(row[0]) for row in sf_cur.fetchall()}
# Result: {1712476800, 1712480400, 1712484000, ...}

# 2. Convert API times from ISO format to epoch seconds
df_times_epoch = pd.to_datetime(df["time"]).astype(int) // 10**9
# "2026-04-07T00:00" → 1712476800
# "2026-04-07T01:00" → 1712480400

# 3. Compare the same types:
sf_new_rows = df[~df_times_epoch.isin(sf_existing)]
# Comparing: 1712476800 vs 1712476800 ✓ MATCHES!
```

**Why this works:** Now we're comparing **epoch numbers to epoch numbers** instead of **strings to strings** (with different formats). The deduplication logic correctly identifies which rows are new.

### Fix #2: Check Datetime Types Before String Types

We reordered the type checks in `snowflake_client.py`:

```python
# New code (correct order):
if val is None or (isinstance(val, float) and pd.isna(val)):
    values.append("NULL")
elif isinstance(val, (datetime, pd.Timestamp)):  # ← Check this FIRST
    epoch_seconds = int(val.timestamp())
    values.append(str(epoch_seconds))
elif isinstance(val, str):  # ← Check this SECOND
    # Handle strings
    ...
```

**Why this works:** Now datetime objects get converted to epoch seconds before they're treated as strings. It's the correct order of operations.

---

## Why These Fixes Worked

### Before the Fix
```
API Data: "2026-04-07T00:00"
    ↓
DataFrame time column: "2026-04-07T00:00"
    ↓
Dedup check: Does "2026-04-07T00:00" exist in Snowflake?
    ↓
Snowflake returns: 1712476800 (epoch seconds)
    ↓
Compare: "2026-04-07T00:00" vs "1712476800" ?
    ↓
No match! (Different formats)
    ↓
❌ Write fails: Data doesn't get inserted
```

### After the Fix
```
API Data: "2026-04-07T00:00"
    ↓
Convert to epoch: 1712476800
    ↓
Dedup check: Does 1712476800 exist in Snowflake?
    ↓
Snowflake returns: 1712476800 (epoch seconds)
    ↓
Compare: 1712476800 vs 1712476800 ✓
    ↓
Match! (Same format)
    ↓
✅ Write succeeds: 168 rows inserted into Snowflake
```

---

## How We Verified the Fix

**Step 1:** We deployed the fixed code to the EC2 server using `scripts/deploy.sh`

**Step 2:** We manually triggered the weather DAG to test it

**Step 3:** We checked the logs and saw:
```
Snowflake has 0 existing timestamps
Snowflake dedup: 0 existing, 168 new rows
Loaded 168 rows into Snowflake WEATHER_HOURLY
```

✅ **Success!** The pipeline now writes all 168 rows to Snowflake every hour.

---

## Why Did This Happen in the First Place?

There were a few layers to this problem:

1. **The table schema was created wrong** (using `write_pandas` with automatic type inference instead of proper TIMESTAMP columns)

2. **We had to work around it with epoch seconds** instead of fixing the schema (due to Snowflake permission issues)

3. **The deduplication logic wasn't updated** to match the workaround (it was comparing different timestamp formats)

4. **Type checking was in the wrong order** in the data insertion code

This is a good example of how **small mismatches between data formats** can cause silent failures that are hard to debug. The data was being *transformed* (timestamps → epoch numbers) but the *comparison logic* wasn't aware of that transformation.

---

## Key Takeaways

1. **Timestamps have many formats:** Date strings, Unix epoch numbers, Timestamp objects in Python — they're all the same moment in time but look completely different. Code that compares them needs to convert them to the same format first.

2. **Type checking order matters:** In Python, checking `isinstance(val, str)` before `isinstance(val, datetime)` means datetime objects get treated as strings, which is wrong.

3. **Workarounds have ripple effects:** When you work around a schema problem (using epoch numbers instead of TIMESTAMP columns), you need to update all the code that touches that data.

4. **Test your assumptions:** The write didn't fail loudly — it silently succeeded (no error message) but wrote 0 rows. This is why we needed to check the logs to understand what happened.
