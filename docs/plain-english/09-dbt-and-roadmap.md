# Part 10–11: dbt and the Step 2 Roadmap

> Part of the [Plain English Guide](README.md)

---

## What Is dbt, and Why Does It Matter?

### The problem dbt solves

Right now your pipeline does three things inside the `transform()` task:
1. Pulls raw data from the API
2. Reshapes it (flattens JSON, renames columns, filters rows) using Python + Pandas
3. Writes the result directly to the database

This works fine for two tables. But imagine 20 tables — every transformation is buried in Python functions spread across DAG files. If a business rule changes, you have to find the right function, edit it, redeploy, and hope nothing broke.

dbt (data build tool) moves all transformation logic into **SQL files** in version control — one file per table, with a clear name and automated tests.

### How dbt fits into your pipeline

```
Stage 1 (Airflow DAG): Extract → Load raw data into Snowflake
Stage 2 (dbt):         Transform raw → clean analytics tables
```

The Airflow DAG handles "get the data in." dbt handles "make the data useful."

### What dbt models look like

Each dbt "model" is just a `.sql` file:
```sql
-- Annual revenue for each company — cleaned and filtered
SELECT ticker, entity_name, period_end, fiscal_year, value AS revenue_usd, filed_date
FROM {{ ref('stg_company_financials') }}
WHERE metric = 'Revenues' AND fiscal_period = 'FY'
ORDER BY ticker, period_end
```

The `{{ ref(...) }}` is dbt's dependency system — it knows to run the staging model first, then this one, building a lineage graph automatically.

### dbt tests — built-in data quality

```yaml
models:
  - name: fct_company_financials
    columns:
      - name: ticker
        tests: [not_null, {accepted_values: {values: ['AAPL', 'MSFT', 'GOOGL']}}]
      - name: revenue_usd
        tests: [not_null]
```

Run `dbt test` and it checks every rule. Failed test = you know immediately.

### How dbt integrates with Airflow

`astronomer-cosmos` turns dbt models into Airflow tasks automatically. Each model becomes its own task in the Airflow UI with its own logs.

---

## The Full Step 2 Roadmap — What Comes Next

### Step 2a: Snowflake

**What:** Replace MariaDB with Snowflake as your database.

**Why:** Snowflake is the dominant cloud data warehouse. It separates storage from compute, scales automatically, and frees ~300–500 MB of RAM on EC2 because MariaDB gets uninstalled.

**Status:** Scaffolding complete (dual-write code, dashboard switch, runbook). Need to sign up for Snowflake (free trial, $400 credits) and run activation.

### Step 2b: dbt (after Snowflake)

**What:** Move transformation logic from Python/Pandas into SQL models with dbt.

**Why:** Cleaner code, automatic lineage, built-in data quality tests, and the industry standard for transformations on top of Snowflake.

### Step 2c: Kafka (after dbt)

**What:** Add a streaming layer between DAGs and Snowflake. Each API response becomes a Kafka event, consumed and written to Snowflake in real time.

**Why:** Demonstrates both batch (current) and streaming pipelines — two distinct, in-demand skill sets. Also more resilient: if Snowflake is down, Kafka buffers events rather than losing them.

**Memory note for t3.large:** Use KRaft mode (no Zookeeper) and `KAFKA_HEAP_OPTS="-Xmx768m -Xms768m"`.

---

## Portfolio Extras

These aren't required but significantly boost resume impact:

| Extra | Why it matters |
|---|---|
| **Architecture diagram** in README | Visual diagrams show systems thinking |
| **GitHub Actions CI/CD** (run `dbt test` on PRs) | Shows DevOps basics |
| **Public dashboard URL** | Recruiters can click and see a live pipeline |
| **Cost callout in README** | Business awareness is rare in junior candidates |
| **dbt docs site** | Auto-generated HTML with lineage graph |
| **Slack alerting** (connected) | Operational maturity |
| **Data quality in dashboard** (counts, freshness) | Product thinking |

**Already impressive things you have:**
- Real data sources (SEC EDGAR, Open-Meteo) — not toy datasets
- Production deployment on AWS (not just localhost)
- Kubernetes orchestration
- Vacation mode kill switch
- PVC-backed logs that survive pod restarts
- Post-quantum SSH on Ubuntu 24.04
