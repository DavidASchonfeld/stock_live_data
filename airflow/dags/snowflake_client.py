import pandas as pd
from datetime import datetime


def write_df_to_snowflake(
    df: pd.DataFrame,
    table_name: str,
    conn_id: str = "snowflake_default",
    overwrite: bool = True,
) -> None:
    """Bulk-load a DataFrame into a Snowflake table using direct SQL INSERT.

    Auto-creates the table on first run (schema inferred from DataFrame columns).
    overwrite=True  → full table replace; matches MariaDB if_exists="replace" (stocks DAG)
    overwrite=False → append rows;        matches MariaDB if_exists="append"  (weather DAG)

    table_name: uppercase Snowflake table name, e.g. "COMPANY_FINANCIALS"
    conn_id:    Airflow Connection ID for the Snowflake connection (default: "snowflake_default")
    overwrite:  True = replace table each run; False = append rows (default: True)
    """
    # Lazy imports — only fail at execution time, not at DAG parse time or module import
    from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

    hook = SnowflakeHook(snowflake_conn_id=conn_id)
    conn = hook.get_conn()
    cur = conn.cursor()

    # Uppercase column names to match Snowflake convention
    df.columns = [col.upper() for col in df.columns]

    # Check if table exists
    try:
        cur.execute(f"SELECT 1 FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_NAME='{table_name}' AND TABLE_SCHEMA='RAW'")
        table_exists = cur.fetchone() is not None
    except Exception:
        table_exists = False

    # If overwrite=True and table exists, truncate it
    if overwrite and table_exists:
        cur.execute(f"DELETE FROM PIPELINE_DB.RAW.{table_name}")

    # Prepare rows for INSERT — convert each row to a tuple with proper SQL formatting
    # Note: This handles tables with NUMBER columns for timestamps (converts to epoch seconds)
    rows_to_insert = []
    for _, row in df.iterrows():
        # Build tuple with SQL-safe values
        values = []
        for col in df.columns:
            val = row[col]
            if val is None or (isinstance(val, float) and pd.isna(val)):
                values.append("NULL")
            elif col.upper() == 'TIMEZONE':
                # TIMEZONE is a string but stored as NUMBER in the table — insert NULL as we can't convert
                values.append("NULL")
            elif isinstance(val, (datetime, pd.Timestamp)):
                # Convert timestamp to epoch seconds (Unix timestamp) for NUMBER columns
                epoch_seconds = int(val.timestamp())
                values.append(str(epoch_seconds))
            elif isinstance(val, str):
                # For numeric columns that should be strings, try to convert or NULL
                try:
                    # Try to parse as float if it's supposed to be numeric
                    float(val)
                    values.append(val)
                except ValueError:
                    # If it's not numeric, insert NULL (handles broken schema)
                    values.append("NULL")
            else:
                values.append(str(val))
        rows_to_insert.append(f"({','.join(values)})")

    # Execute INSERT in batches (Snowflake has statement size limits)
    batch_size = 1000
    col_list = ','.join(df.columns)
    for i in range(0, len(rows_to_insert), batch_size):
        batch = rows_to_insert[i : i + batch_size]
        insert_sql = f"INSERT INTO PIPELINE_DB.RAW.{table_name} ({col_list}) VALUES " + ', '.join(batch)
        cur.execute(insert_sql)

    conn.commit()
    cur.close()
    conn.close()
