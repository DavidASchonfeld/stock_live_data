# Kafka Commit Timing — Misleading Comments

**Date discovered:** 2026-04-12
**Severity:** Low (no data loss in practice — but comment described behavior that didn't exist)
**Files affected:** `airflow/dags/dag_stocks_consumer.py`, `airflow/dags/dag_weather_consumer.py`

---

## What Happened

Both consumer DAGs had a comment claiming the Kafka offset commit happened "only after Snowflake write." In reality the commit happened right after reading from Kafka — two steps before the Snowflake write even ran. The comment was misleading, not the code.

---

## Plain English: What Is a Kafka Offset Commit?

Kafka stores messages on a numbered tape. Each consumer group has a bookmark saying "I've read up to position #N." Calling `consumer.commit()` moves that bookmark forward. The next time the consumer runs, it starts from #N+1 — it won't see the old messages again.

If you commit too early (before processing succeeds) and then processing fails, those messages are gone from Kafka's perspective. Kafka won't replay them.

---

## Why It Wasn't Actually a Problem

Two layers of protection already existed that made the early commit safe:

**Stocks pipeline:**
- Daily batch gate (`SF_STOCKS_LAST_WRITE_DATE` Airflow Variable) blocks writes if Snowflake was already written to today
- `if_exists="replace"` strategy — EDGAR sends all historical data every call, so re-processing produces identical results

**Weather pipeline:**
- Daily batch gate (`SF_WEATHER_LAST_WRITE_DATE` Airflow Variable) blocks writes if already written today
- Timestamp dedup: explicitly queries Snowflake for existing timestamps and only inserts new rows

So even if a message was re-read (e.g., after deploy-time offset reset), neither pipeline would write duplicate data.

---

## What We Changed

Updated the comments in both consumer DAGs to honestly describe what the code actually does:

| Before | After |
|---|---|
| `# manual commit: offset advances only after Snowflake write` | `# manual commit: we control when to advance the bookmark` |
| `# advance offset after reading` | `# commit here (before Snowflake write); daily gate + [dedup strategy] prevent duplicates` |
| Docstring: "Commits offset only after successful read — prevents message loss on retry" | Docstring: explains commit is before Snowflake write, and which safety layers make this safe |

No code logic changed — only comments.

---

## Design Decision: Why We Kept the Early Commit (Option C)

There were three options considered:

| Option | Description | Verdict |
|---|---|---|
| A | Move commit to after Snowflake write | Correct in theory, but impossible — two separate Airflow tasks can't share a live Kafka consumer object |
| B | Don't commit at all, rely on deploy-time offset reset | Fragile, misleading comment still an issue |
| C | Keep early commit, rely on existing dedup safety nets | Pragmatic, honest, matches actual architecture |

We chose Option C. The architecture already handles idempotency at the Snowflake layer, so the early commit is safe. Trying to move the commit later would require merging two Airflow tasks into one large task, making the code harder to read and test.

---

## Lessons

- Comments that describe intent ("we want X") instead of behavior ("this does Y") can survive code changes and drift from reality
- In a two-task Airflow pipeline (consume → write), it is architecturally impossible to commit after the write without merging the tasks
- Idempotent write strategies (replace, dedup) are a valid substitute for perfect commit ordering when the architecture doesn't allow perfect ordering
