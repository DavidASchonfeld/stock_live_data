#!/bin/bash
# Module: snowflake — run scripts/snowflake_setup.sql against a fresh or existing Snowflake account.
# Sourced by deploy.sh; all variables from common.sh are available here.
#
# Required env vars in .env.deploy (in addition to the normal SNOWFLAKE_* service-account vars):
#   SNOWFLAKE_ADMIN_USER     — your personal Snowflake login (needs SYSADMIN or equivalent)
#   SNOWFLAKE_ADMIN_PASSWORD — admin password (never committed)
#   SNOWFLAKE_ACCOUNT        — account identifier, e.g. abc12345.us-east-1
#   SNOWFLAKE_PASSWORD       — desired password for PIPELINE_USER (injected into the SQL at run time)
#
# The script uses snowflake-connector-python (already a project dependency — installed in the
# anomaly-detection ml-venv and available via pip in most environments).

step_snowflake_setup() {
    echo "=== Snowflake Setup: applying scripts/snowflake_setup.sql ==="

    # Verify the required admin credentials are present before attempting a connection
    for var in SNOWFLAKE_ACCOUNT SNOWFLAKE_ADMIN_USER SNOWFLAKE_ADMIN_PASSWORD SNOWFLAKE_PASSWORD; do
        if [ -z "${!var:-}" ]; then
            echo "ERROR: $var is not set in .env.deploy — required for --snowflake-setup"
            echo "  SNOWFLAKE_ADMIN_USER / SNOWFLAKE_ADMIN_PASSWORD — your personal SYSADMIN credentials"
            echo "  SNOWFLAKE_PASSWORD                              — desired password for PIPELINE_USER"
            exit 1
        fi
    done

    # Read the SQL file and inject the PIPELINE_USER password before sending to Snowflake.
    # {{SNOWFLAKE_PASSWORD}} is a placeholder in the SQL — we replace it here so the password
    # is never stored in the file itself (which IS committed to git).
    SQL_FILE="$PROJECT_ROOT/scripts/snowflake_setup.sql"
    if [ ! -f "$SQL_FILE" ]; then
        echo "ERROR: SQL setup file not found: $SQL_FILE"
        exit 1
    fi

    # Run the setup SQL via Python (snowflake-connector-python is already a project dependency)
    python3 - <<PYTHON
import snowflake.connector
import os
import sys

# Read the SQL file and replace the password placeholder with the real value
sql_raw = open("$SQL_FILE").read()
sql_final = sql_raw.replace("{{SNOWFLAKE_PASSWORD}}", os.environ["SNOWFLAKE_PASSWORD"])

# Connect as SYSADMIN so we can create warehouses, databases, roles, and users
print("Connecting to Snowflake as SYSADMIN...")
conn = snowflake.connector.connect(
    account=os.environ["SNOWFLAKE_ACCOUNT"],
    user=os.environ["SNOWFLAKE_ADMIN_USER"],
    password=os.environ["SNOWFLAKE_ADMIN_PASSWORD"],
    role="SYSADMIN",
)

cur = conn.cursor()

# Split on semicolons; skip blank lines and comment-only lines so we don't send empty statements
statements = [s.strip() for s in sql_final.split(";") if s.strip()]
statements = [s for s in statements if not all(line.startswith("--") for line in s.splitlines() if line.strip())]

total = len(statements)
print(f"Executing {total} SQL statements...")

for i, stmt in enumerate(statements, 1):
    # Show the first line of each statement so progress is readable in the log
    preview = stmt.splitlines()[0].strip()
    print(f"  [{i}/{total}] {preview}")
    cur.execute(stmt)

conn.close()
print("Snowflake setup complete — all objects created/verified.")
PYTHON

    echo "=== Snowflake Setup: done ==="
}
