# Standalone anomaly detection script — runs under /opt/ml-venv (scikit-learn + mlflow available)
# Reads FCT_COMPANY_FINANCIALS, fits IsolationForest on YoY pct changes, writes to FCT_ANOMALIES

import os
import json
import argparse

import pandas as pd
import snowflake.connector                        # direct connector — no Airflow dependency
from sklearn.ensemble import IsolationForest     # ML model for unsupervised anomaly detection
import mlflow                                    # experiment tracking + model artifact logging
import mlflow.sklearn
from mlflow.models import infer_signature          # builds explicit input+output schema to silence int-column warning


# ── Snowflake connection ─────────────────────────────────────────────────────

def get_snowflake_conn():
    """Open a Snowflake connection using env vars — avoids Airflow hook dependency."""
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        database=os.environ["SNOWFLAKE_DATABASE"],
        warehouse=os.environ["SNOWFLAKE_WAREHOUSE"],
        role=os.environ.get("SNOWFLAKE_ROLE", "PIPELINE_ROLE"),  # explicit role — prevents default-role from silently locking created objects
    )


# ── Data fetching & feature engineering ─────────────────────────────────────

def fetch_data(conn) -> pd.DataFrame:
    """
    Pull FY Revenues + NetIncomeLoss from the mart, pivot to wide format,
    compute YoY % change per ticker, and drop the first year (NaN row).
    """
    query = """
        SELECT ticker, fiscal_year, metric, value
        FROM PIPELINE_DB.MARTS.FCT_COMPANY_FINANCIALS
        WHERE UPPER(metric) IN ('REVENUEFROMCONTRACTWITHCUSTOMEREXCLUDINGASSESSEDTAX', 'NETINCOMELOSS')  -- matches XBRL concept fetched by edgar_client.py
          AND fiscal_period = 'FY'
    """
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    cols = [desc[0].lower() for desc in cur.description]   # lowercase column names for consistency
    df = pd.DataFrame(rows, columns=cols)

    df["metric"] = df["metric"].str.lower()  # normalize metric case before pivot to avoid rename mismatch

    # Pivot: one row per (ticker, fiscal_year), columns = revenue, net_income
    wide = df.pivot_table(
        index=["ticker", "fiscal_year"],
        columns="metric",
        values="value",
        aggfunc="first",
    ).reset_index()
    wide.columns.name = None                                 # drop the leftover 'metric' axis name
    wide = wide.rename(columns={
        "revenuefromcontractwithcustomerexcludingassessedtax": "revenue",  # XBRL name from edgar_client.py, lowercased
        "netincomeloss": "net_income",  # lowercased by str.lower() above — was "NetIncomeLoss"
    })

    # Sort so pct_change() computes correctly within each ticker group
    wide = wide.sort_values(["ticker", "fiscal_year"]).reset_index(drop=True)

    # YoY % change computed per ticker — first year per ticker becomes NaN
    wide[["revenue_yoy_pct", "net_income_yoy_pct"]] = (
        wide.groupby("ticker")[["revenue", "net_income"]].pct_change(fill_method=None)  # fill_method=None: explicit no-ffill, suppresses FutureWarning from deprecated default
    )

    # Drop first year per ticker (NaN YoY) — no baseline to compare against
    wide = wide.dropna(subset=["revenue_yoy_pct", "net_income_yoy_pct"]).reset_index(drop=True)

    return wide


# ── Model training + MLflow logging ─────────────────────────────────────────

def run_model(df: pd.DataFrame, contamination: float, n_estimators: int) -> tuple[pd.DataFrame, str]:
    """
    Fit IsolationForest on YoY features, annotate df with results, log to MLflow.
    Returns (annotated_df, mlflow_run_id).
    """
    # Keep as DataFrame (not .values) so sklearn remembers feature names — prevents "fitted without feature names" warning
    features_df = df[["revenue_yoy_pct", "net_income_yoy_pct"]]

    mlflow.set_tracking_uri(os.environ["MLFLOW_TRACKING_URI"])         # point at the MLflow server
    # Restore soft-deleted experiment if present — set_experiment cannot reuse deleted experiments
    _client = mlflow.tracking.MlflowClient()
    _exp = _client.get_experiment_by_name("anomaly_detection")
    if _exp is not None and _exp.lifecycle_stage == "deleted":
        # Only restore if artifact root is the HTTP proxy — skip stale local-path experiments
        if _exp.artifact_location == "mlflow-artifacts:/":
            _client.restore_experiment(_exp.experiment_id)
    mlflow.set_experiment("anomaly_detection")                         # group all runs under one experiment

    with mlflow.start_run():
        model = IsolationForest(
            contamination=contamination,
            n_estimators=n_estimators,
            random_state=42,               # reproducibility
        )
        model.fit(features_df)

        # predict(): 1 = normal, -1 = anomaly; convert to bool for readability
        df["is_anomaly"] = model.predict(features_df) == -1
        # score_samples(): lower (more negative) = more anomalous
        df["anomaly_score"] = model.score_samples(features_df)

        n_anomalies = int(df["is_anomaly"].sum())
        n_total = len(df)

        # Log hyperparams + metrics for experiment comparison
        mlflow.log_param("contamination", contamination)
        mlflow.log_param("n_estimators", n_estimators)
        mlflow.log_metric("n_anomalies", n_anomalies)
        mlflow.log_metric("n_total", n_total)
        mlflow.log_metric("contamination_rate", n_anomalies / n_total if n_total else 0)

        # Cast input sample to float64 so MLflow infers float input schema (not int)
        input_ex = features_df.iloc[:5].astype("float64")
        # Cast predict output to float — IsolationForest.predict() returns int64 ([1,-1]) which triggers MLflow's int-column warning
        sig = infer_signature(input_ex, model.predict(features_df).astype(float))
        # Persist model artifact with explicit signature — suppresses the int-column UserWarning and enables input validation at inference
        mlflow.sklearn.log_model(model, "isolation_forest", input_example=input_ex, signature=sig)

        run_id = mlflow.active_run().info.run_id

    df["mlflow_run_id"] = run_id    # tag every result row with the run that produced it
    return df, run_id


# ── Snowflake write ──────────────────────────────────────────────────────────

def write_results(conn, df: pd.DataFrame) -> None:
    """
    Create ANALYTICS schema + FCT_ANOMALIES table if missing, then full-refresh via DELETE + INSERT.
    """
    cur = conn.cursor()

    # Create schema + table only if they don't already exist
    cur.execute("CREATE SCHEMA IF NOT EXISTS PIPELINE_DB.ANALYTICS")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS PIPELINE_DB.ANALYTICS.FCT_ANOMALIES (
            ticker          VARCHAR,
            fiscal_year     NUMBER,
            revenue_yoy_pct FLOAT,
            net_income_yoy_pct FLOAT,
            is_anomaly      BOOLEAN,
            anomaly_score   FLOAT,
            detected_at     TIMESTAMP_NTZ,
            mlflow_run_id   VARCHAR
        )
    """)

    # Full-refresh: wipe previous run's results before inserting new ones
    cur.execute("DELETE FROM PIPELINE_DB.ANALYTICS.FCT_ANOMALIES")

    # Build rows as tuples for executemany — CURRENT_TIMESTAMP() resolved server-side
    insert_sql = """
        INSERT INTO PIPELINE_DB.ANALYTICS.FCT_ANOMALIES
            (ticker, fiscal_year, revenue_yoy_pct, net_income_yoy_pct,
             is_anomaly, anomaly_score, detected_at, mlflow_run_id)
        VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP(), %s)
    """
    rows = [
        (
            row["ticker"],
            int(row["fiscal_year"]),
            float(row["revenue_yoy_pct"]),
            float(row["net_income_yoy_pct"]),
            bool(row["is_anomaly"]),
            float(row["anomaly_score"]),
            str(row["mlflow_run_id"]),
        )
        for _, row in df.iterrows()
    ]
    cur.executemany(insert_sql, rows)
    conn.commit()    # commit after insert to persist the transaction


# ── CLI + pipeline orchestration ─────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """Parse CLI args so Airflow can override contamination and n_estimators."""
    parser = argparse.ArgumentParser(description="IsolationForest anomaly detection for stock financials")
    parser.add_argument("--contamination", type=float, default=0.05,   # expected anomaly fraction
                        help="IsolationForest contamination (default 0.05)")
    parser.add_argument("--n-estimators", type=int, default=100,       # number of trees in the forest
                        help="IsolationForest n_estimators (default 100)")
    return parser.parse_args()


def run_pipeline(contamination: float, n_estimators: int) -> dict:
    """
    Full pipeline: connect → fetch → model → write → return summary dict.
    Isolated in a function so it can be unit-tested without __main__.
    """
    conn = get_snowflake_conn()
    try:
        df = fetch_data(conn)                                          # pull + engineer features
        df, run_id = run_model(df, contamination, n_estimators)       # fit model + log to MLflow
        write_results(conn, df)                                        # persist to Snowflake
    finally:
        conn.close()    # always release connection even on error

    return {
        "n_anomalies": int(df["is_anomaly"].sum()),
        "n_total": len(df),
        "mlflow_run_id": run_id,
    }


if __name__ == "__main__":
    args = parse_args()
    summary = run_pipeline(args.contamination, args.n_estimators)
    print(json.dumps(summary))    # last line of stdout — Airflow task parses this as the return value
