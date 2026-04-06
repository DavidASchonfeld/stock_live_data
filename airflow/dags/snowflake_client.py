import pandas as pd


def write_df_to_snowflake(
    df: pd.DataFrame,
    table_name: str,
    conn_id: str = "snowflake_default",
    overwrite: bool = True,
) -> None:
    """Bulk-load a DataFrame into a Snowflake table using write_pandas.

    Auto-creates the table on first run (schema inferred from DataFrame columns).
    overwrite=True  → full table replace; matches MariaDB if_exists="replace" (stocks DAG)
    overwrite=False → append rows;        matches MariaDB if_exists="append"  (weather DAG)

    table_name: uppercase Snowflake table name, e.g. "COMPANY_FINANCIALS"
    conn_id:    Airflow Connection ID for the Snowflake connection (default: "snowflake_default")
    overwrite:  True = replace table each run; False = append rows (default: True)
    """
    # Lazy imports — only fail at execution time, not at DAG parse time or module import
    from snowflake.connector.pandas_tools import write_pandas
    from airflow.providers.snowflake.hooks.snowflake import SnowflakeHook

    hook = SnowflakeHook(snowflake_conn_id=conn_id)
    conn = hook.get_conn()

    # Snowflake requires uppercase column names when using write_pandas
    df.columns = [col.upper() for col in df.columns]

    success, nchunks, nrows, _ = write_pandas(
        conn=conn,
        df=df,
        table_name=table_name,
        auto_create_table=True,
        overwrite=overwrite,  # caller controls replace vs. append to match MariaDB behavior
    )
    conn.close()

    if not success:
        raise RuntimeError(f"write_pandas to {table_name} failed ({nchunks} chunks, {nrows} rows written)")
