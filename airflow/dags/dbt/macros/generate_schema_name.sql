-- Override dbt's default schema naming — without this, +schema: staging becomes DBT_OUTPUT_STAGING (wrong)
-- With this macro, +schema: staging resolves directly to STAGING, matching the Snowflake schema names
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}       {# fall back to target.schema (DBT_OUTPUT) when no +schema is set #}
    {%- else -%}
        {{ custom_schema_name | upper }}  {# use the custom schema name as-is, uppercased for Snowflake #}
    {%- endif -%}
{%- endmacro %}
