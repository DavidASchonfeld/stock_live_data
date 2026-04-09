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

# OpenLineage config — console transport writes events to stdout (visible in Airflow task logs)
# To use Marquez instead, replace "console" with: type: http\n  config:\n    url: http://marquez:5000
COPY --chown=airflow:root openlineage.yml /opt/openlineage.yml

USER airflow
