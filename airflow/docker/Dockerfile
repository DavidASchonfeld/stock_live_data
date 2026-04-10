# must match the tag in values.yaml exactly
FROM apache/airflow:3.1.8

# root needed to install python3-venv and create the virtualenv
USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# /opt/dbt-venv is isolated from Airflow's Python — dbt pins SQLAlchemy < 2.0, Airflow 3.x requires >= 2.0
# baking into the image ensures the venv survives pod restarts (runtime installs disappear on restart)
RUN python3 -m venv /opt/dbt-venv \
    && /opt/dbt-venv/bin/pip install --upgrade pip \
    && /opt/dbt-venv/bin/pip install \
         dbt-core==1.8.0 \
         dbt-snowflake==1.8.0 \
         openlineage-dbt \
    && chown -R airflow: /opt/dbt-venv

# /opt/ml-venv: isolated venv for MLflow + ML libs — avoids conflicts with Airflow's pinned deps
# setuptools pinned <75: setuptools 80+ removed pkg_resources; mlflow 2.15.1 requires it at runtime
RUN python3 -m venv /opt/ml-venv \
    && /opt/ml-venv/bin/pip install --upgrade pip \
    && /opt/ml-venv/bin/pip install \
         mlflow==2.15.1 \
         scikit-learn==1.5.2 \
         pandas==2.2.2 \
         snowflake-connector-python==3.10.1 \
    && /opt/ml-venv/bin/pip install "setuptools<75" \
    && /opt/ml-venv/bin/python -c "import pkg_resources" \
    && chown -R airflow: /opt/ml-venv

# OpenLineage config — console transport writes events to stdout (visible in Airflow task logs)
# To use Marquez instead, replace "console" with: type: http\n  config:\n    url: http://marquez:5000
COPY --chown=airflow:root openlineage.yml /opt/openlineage.yml

USER airflow
