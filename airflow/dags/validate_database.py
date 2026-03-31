"""
Database Validation Script

Verifies that stock_daily_prices and weather_hourly tables exist with correct
schemas, and provides a summary of data freshness and row counts.

Usage:
  python validate_database.py

This script can be run manually from EC2 or scheduled as an Airflow task to
auto-validate data after DAG runs complete.
"""

import sys
from datetime import datetime
from pprint import pformat

import pandas as pd
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.exc import SQLAlchemyError

from file_logger import OutputTextWriter
from db_config import DB_USER, DB_PASSWORD, DB_NAME, DB_HOST


# Expected schema for stock_daily_prices table
EXPECTED_SCHEMA_STOCK = {
    "ticker": "VARCHAR",
    "date": "VARCHAR",
    "open": "FLOAT",
    "high": "FLOAT",
    "low": "FLOAT",
    "close": "FLOAT",
    "volume": "INT",
}

# Expected schema for weather_hourly table
EXPECTED_SCHEMA_WEATHER = {
    "time": "VARCHAR",
    "temperature_2m": "FLOAT",
    "latitude": "FLOAT",
    "longitude": "FLOAT",
    "elevation": "FLOAT",
    "timezone": "VARCHAR",
    "utc_offset_seconds": "INT",
    "imported_at": "VARCHAR",
}


def validate_database():
    """Main validation routine — checks connection, tables, schemas, and data freshness."""

    # Initialize logger for dual output (stdout + PVC file in K8s pod, or local file on EC2)
    # Dual logging allows debugging from both pod logs and persistent files
    try:
        writer = OutputTextWriter("/opt/airflow/out")  # K8s pod path (PVC-mounted)
    except PermissionError:
        writer = OutputTextWriter("/tmp")  # Fallback for local EC2 execution (if PVC unavailable)
    writer.print("=" * 80)
    writer.print(f"Database Validation Started: {datetime.now()}")
    writer.print("=" * 80)

    try:
        # Create database engine using existing db_config credentials
        engine = create_engine(f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}")

        # Test basic connection
        with engine.connect() as connection:
            result = connection.execute(text("SELECT 1"))
            writer.print("✓ Database connection successful")

        # Use SQLAlchemy Inspector to examine schema
        inspector = inspect(engine)
        tables = inspector.get_table_names()
        writer.print(f"\n✓ Tables in database: {tables}")

        # Validate stock_daily_prices table
        writer.print("\n" + "─" * 80)
        writer.print("TABLE: stock_daily_prices")
        writer.print("─" * 80)
        _validate_table(engine, inspector, "stock_daily_prices", EXPECTED_SCHEMA_STOCK, writer)

        # Validate weather_hourly table
        writer.print("\n" + "─" * 80)
        writer.print("TABLE: weather_hourly")
        writer.print("─" * 80)
        _validate_table(engine, inspector, "weather_hourly", EXPECTED_SCHEMA_WEATHER, writer)

        writer.print("\n" + "=" * 80)
        writer.print(f"Database Validation Completed: {datetime.now()}")
        writer.print("=" * 80)

        return True

    except SQLAlchemyError as e:
        writer.print(f"✗ Database error: {str(e)}")
        raise
    except Exception as e:
        writer.print(f"✗ Validation error: {str(e)}")
        raise


def _validate_table(engine, inspector, table_name: str, expected_schema: dict, writer):
    """Helper function to validate a single table's schema and data freshness."""

    try:
        # Check if table exists
        # Fails early if DAG hasn't run yet or table was accidentally dropped
        if table_name not in inspector.get_table_names():
            writer.print(f"✗ Table {table_name} does NOT exist")
            return False

        writer.print(f"✓ Table exists")

        # Get actual schema
        # Inspector queries the database metadata; detects schema changes (dropped/renamed columns)
        columns = inspector.get_columns(table_name)
        actual_columns = {col["name"]: col["type"] for col in columns}

        writer.print(f"\n  Columns found:")
        for col_name, col_type in actual_columns.items():
            writer.print(f"    - {col_name}: {col_type}")

        # Check for expected columns (not strict — extras are OK)
        # Detects if critical columns were accidentally deleted; warns on schema drift
        missing_columns = set(expected_schema.keys()) - set(actual_columns.keys())
        if missing_columns:
            writer.print(f"\n  ✗ Missing expected columns: {missing_columns}")
        else:
            writer.print(f"\n  ✓ All expected columns present")

        # Get row count
        # Increasing row count over time = data flowing; zero rows = data not arriving
        with engine.connect() as conn:
            count_result = conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
            row_count = count_result.scalar()
            writer.print(f"  ✓ Row count: {row_count} rows")

            # Get freshness info (latest date/time in table)
            # Stale timestamps indicate DAG failure; fresh data means pipeline is healthy
            if table_name == "stock_daily_prices":
                freshness = conn.execute(
                    text(f"SELECT MAX(date) as latest_date FROM {table_name}")
                ).scalar()
                writer.print(f"  ✓ Latest data date: {freshness}")

            elif table_name == "weather_hourly":
                freshness = conn.execute(
                    text(f"SELECT MAX(time) as latest_time FROM {table_name}")
                ).scalar()
                writer.print(f"  ✓ Latest data time: {freshness}")

        return True

    except SQLAlchemyError as e:
        writer.print(f"  ✗ Error validating table: {str(e)}")
        return False


if __name__ == "__main__":
    try:
        success = validate_database()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"Validation failed: {e}")
        sys.exit(1)
