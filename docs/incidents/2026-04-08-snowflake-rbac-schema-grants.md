# Snowflake RBAC Schema Grants Incident — April 8, 2026

## What Went Wrong

`dbt_run` failed with Snowflake error 003041:

```
Schema 'STAGING' already exists, but current role has no privileges on it.
```

dbt runs `CREATE SCHEMA IF NOT EXISTS` at startup. Snowflake raises 003041 when the schema exists but was created by a different role — even with `IF NOT EXISTS`, the calling role must own the schema.

Additionally, the initial GRANT attempt failed because the role was specified as `PIPELINE_USER` (the login name) instead of `PIPELINE_ROLE` (the actual role).

---

## Root Cause

- `STAGING` and `MARTS` schemas were created by `ACCOUNTADMIN`, not `PIPELINE_ROLE`
- `PIPELINE_ROLE` had no privileges on either schema
- dbt's schema creation step hit 003041 before any models could run

---

## How to Find the Correct Role Name

```sql
DESC USER PIPELINE_USER;
-- Look at the `default_role` field → PIPELINE_ROLE
```

---

## Fix

Run in Snowsight as `ACCOUNTADMIN`:

**Step 1 — Grant schema privileges:**
```sql
GRANT USAGE ON SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;
GRANT CREATE VIEW ON SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;

GRANT USAGE ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
GRANT CREATE VIEW ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
```

**Step 2 — Transfer ownership (required for dbt's CREATE SCHEMA step):**
```sql
GRANT OWNERSHIP ON SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE COPY CURRENT GRANTS;
GRANT OWNERSHIP ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE COPY CURRENT GRANTS;
```

`COPY CURRENT GRANTS` preserves the privileges granted in Step 1.

---

## Verification

Re-trigger `dbt_run` in the Airflow UI. Confirm no exit code 2 and models appear in `PIPELINE_DB.STAGING` / `PIPELINE_DB.MARTS` in Snowsight.

---

**Date:** 2026-04-08
**Affected component:** Snowflake RBAC — STAGING and MARTS schemas
**Resolution:** Two GRANT statements in Snowsight, no code changes required
