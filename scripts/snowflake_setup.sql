-- Snowflake infrastructure setup — idempotent, safe to run on any fresh or existing account.
-- Run via: ./scripts/deploy.sh --snowflake-setup
-- Requires a SYSADMIN (or equivalent) user in .env.deploy.
--
-- What this script creates:
--   PIPELINE_WH     — compute warehouse (X-SMALL, auto-suspends after 60s to save cost)
--   PIPELINE_DB     — main database
--   RAW schema      — landing zone; written by Airflow DAGs
--   STAGING schema  — dbt views built from RAW (auto-created by dbt, but pre-granted here)
--   MARTS schema    — dbt fact/dim tables for the dashboard (auto-created by dbt, but pre-granted here)
--   ANALYTICS schema— anomaly detection results; auto-created by anomaly_detector.py on first run
--   PIPELINE_ROLE   — least-privilege service role used by all pipeline components
--   PIPELINE_USER   — service account; password set via SNOWFLAKE_PASSWORD env var (never committed)
--
-- Schemas NOT created here (created automatically at runtime):
--   ANALYTICS — anomaly_detector.py runs CREATE SCHEMA IF NOT EXISTS on first execution
--
-- After running this script, the pipeline auto-populates tables on the first DAG run:
--   RAW.COMPANY_FINANCIALS  — written by dag_stocks_consumer.py via snowflake_client.py
--   RAW.WEATHER_HOURLY      — written by dag_weather_consumer.py via snowflake_client.py
--   STAGING views           — created by dbt on first `dbt run`
--   MARTS tables            — created by dbt on first `dbt run`
--   ANALYTICS.FCT_ANOMALIES — created by anomaly_detector.py on first anomaly detection run

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Warehouse — X-SMALL is cheapest; auto-suspend at 60s stops billing when idle
-- ─────────────────────────────────────────────────────────────────────────────
CREATE WAREHOUSE IF NOT EXISTS PIPELINE_WH
    WAREHOUSE_SIZE = 'X-SMALL'
    AUTO_SUSPEND   = 60
    AUTO_RESUME    = TRUE
    COMMENT        = 'Pipeline compute warehouse — all DAGs, dbt runs, and dashboard queries use this';

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Database — single database holds all pipeline schemas
-- ─────────────────────────────────────────────────────────────────────────────
CREATE DATABASE IF NOT EXISTS PIPELINE_DB
    COMMENT = 'Main data pipeline database';

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. Schemas — RAW, STAGING, MARTS created here; ANALYTICS is created at runtime
-- ─────────────────────────────────────────────────────────────────────────────
-- RAW: landing zone for raw data written by Airflow DAG tasks
CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.RAW
    COMMENT = 'Raw landing zone — written by Airflow DAGs, read by dbt';

-- STAGING: dbt views that clean and cast RAW data (dbt creates these, but role needs access pre-granted)
CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.STAGING
    COMMENT = 'dbt staging views — cleaned and typed from RAW';

-- MARTS: dbt fact/dim tables queried by the Flask dashboard
CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.MARTS
    COMMENT = 'dbt mart tables — deduplicated, dashboard-ready';

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. Role — service role with least privilege; granted to PIPELINE_USER below
-- ─────────────────────────────────────────────────────────────────────────────
CREATE ROLE IF NOT EXISTS PIPELINE_ROLE
    COMMENT = 'Service role for all pipeline components (Airflow, dbt, dashboard, anomaly detector)';

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. Warehouse grant — role must be able to use the warehouse to run queries
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE ON WAREHOUSE PIPELINE_WH TO ROLE PIPELINE_ROLE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. Database grant — role must be able to see the database
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE ON DATABASE PIPELINE_DB TO ROLE PIPELINE_ROLE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. RAW schema grants — Airflow DAGs write raw data here
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE        ON SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
-- Current tables (covers any tables already present when this runs)
GRANT INSERT, UPDATE, SELECT, DELETE ON ALL    TABLES IN SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;
-- Future tables (covers tables created on the first DAG run — must grant before tables exist)
GRANT INSERT, UPDATE, SELECT, DELETE ON FUTURE TABLES IN SCHEMA PIPELINE_DB.RAW TO ROLE PIPELINE_ROLE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. STAGING schema grants — dbt builds views here; dashboard doesn't query STAGING directly
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE        ON SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON ALL    TABLES IN SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON FUTURE TABLES IN SCHEMA PIPELINE_DB.STAGING TO ROLE PIPELINE_ROLE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 9. MARTS schema grants — dbt builds fact/dim tables here; Flask dashboard queries these
-- ─────────────────────────────────────────────────────────────────────────────
GRANT USAGE        ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
GRANT CREATE TABLE ON SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON ALL    TABLES IN SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;
GRANT INSERT, UPDATE, SELECT, DELETE ON FUTURE TABLES IN SCHEMA PIPELINE_DB.MARTS TO ROLE PIPELINE_ROLE;

-- ─────────────────────────────────────────────────────────────────────────────
-- 10. PIPELINE_USER — service account used by all pipeline components
--     Password is injected by the runner script from SNOWFLAKE_PASSWORD env var.
--     The literal string {{SNOWFLAKE_PASSWORD}} is replaced before execution — never committed.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE USER IF NOT EXISTS PIPELINE_USER
    PASSWORD          = '{{SNOWFLAKE_PASSWORD}}'
    DEFAULT_ROLE      = PIPELINE_ROLE
    DEFAULT_WAREHOUSE = PIPELINE_WH
    DEFAULT_NAMESPACE = 'PIPELINE_DB.RAW'
    COMMENT           = 'Service account for all pipeline components';

-- Bind the service role to the service user
GRANT ROLE PIPELINE_ROLE TO USER PIPELINE_USER;
