import pandas as pd
from snowflake.connector.pandas_tools import write_pandas
from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook


def write_df_to_snowflake(df: pd.DataFrame, table_name: str, conn_id: str = "snowflake_default") -> None:
    """Bulk-load a DataFrame into a Snowflake table using write_pandas.

    Auto-creates the table on first run (schema inferred from DataFrame columns).
    Uses overwrite=True — equivalent to if_exists="replace" in MariaDB loads.

    table_name: uppercase Snowflake table name, e.g. "COMPANY_FINANCIALS"
    conn_id:    Airflow Connection ID for the Snowflake connection (default: "snowflake_default")
    """
    hook = SnowflakeHook(snowflake_conn_id=conn_id)
    conn = hook.get_conn()

    # Snowflake requires uppercase column names when using write_pandas
    df.columns = [col.upper() for col in df.columns]

    success, nchunks, nrows, _ = write_pandas(
        conn=conn,
        df=df,
        table_name=table_name,
        auto_create_table=True,
        overwrite=True,
    )
    conn.close()

    if not success:
        raise RuntimeError(f"write_pandas to {table_name} failed ({nchunks} chunks, {nrows} rows written)")
