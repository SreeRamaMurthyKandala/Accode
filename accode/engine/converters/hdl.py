"""Hive DDL (.hdl) → BigQuery DDL converter."""
import re
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are a database engineer migrating Hive DDL table definitions to BigQuery DDL.

Convert the Hive DDL to BigQuery `CREATE TABLE` statements following these rules.

## Table creation
- The fully-qualified BigQuery table reference (e.g. `\`my-project.my_dataset.my_table\``)
  is supplied in the user message as `TARGET_TABLE_REF`. Use it VERBATIM — do NOT
  emit literal placeholders like `project.dataset.name`.
- `DROP TABLE IF EXISTS name;` → `DROP TABLE IF EXISTS <TARGET_TABLE_REF>;`
- `CREATE EXTERNAL TABLE name (...)` → `CREATE TABLE IF NOT EXISTS <TARGET_TABLE_REF> (`
- Keep column names exactly as-is

## Remove these Hive-specific clauses entirely
- `ROW FORMAT DELIMITED FIELDS TERMINATED BY ...`
- `LINES TERMINATED BY ...`
- `STORED AS TEXTFILE` / `STORED AS PARQUET` / `STORED AS ORC`
- `TBLPROPERTIES (...)`
- `COMMENT '...'` at the column level (move to inline SQL comment `-- description`)

## Table-level COMMENT
- Convert to a SQL comment: `-- Description: <comment text>`

## LOCATION
- Convert to a SQL comment: `-- GCS Location: <gcs_path>` (path already substituted)

## Partitioning
BigQuery `PARTITION BY` requires the partition expression to be a DATE, TIMESTAMP,
DATETIME, or INT64 *column* — it does NOT accept function calls on STRING columns
like `DATE(PARSE_DATE(..., str_col))`. Changing a STRING partition column to DATE
would also break downstream HQL queries that compare it to string literals, so
the safe default is to OMIT partitioning and let the user add it manually.

Rules:
- If partition column is already DATE / TIMESTAMP / DATETIME → `PARTITION BY col`
- If partition column is STRING (any format), or you are unsure → OMIT the
  `PARTITION BY` clause entirely. Add this comment above the CREATE TABLE:
  `-- TODO: partitioning omitted — original Hive partition was on <col> (<type>). Add a BigQuery PARTITION BY manually after deciding whether to retype the column to DATE/TIMESTAMP or use an ingestion-time partition.`
- Keep the partition column itself in the column list — only the PARTITION BY clause is omitted.

## Data type mappings
- STRING → STRING
- DECIMAL(p,s) → NUMERIC (p ≤ 29) or BIGNUMERIC (p > 29)
- BIGINT → INT64
- INT → INT64
- SMALLINT → INT64
- TINYINT → INT64
- FLOAT → FLOAT64
- DOUBLE → FLOAT64
- BOOLEAN → BOOL
- TIMESTAMP → TIMESTAMP
- DATE → DATE
- BINARY → BYTES
- ARRAY<type> → ARRAY<bq_type>
- MAP<k,v> → JSON  -- add `-- TODO: MAP type converted to JSON; validate downstream usage`
- STRUCT<...> → STRUCT<...> (keep, BQ supports STRUCT)

## Output format
- Return ONLY the BigQuery DDL SQL — no markdown fences, no explanation
- Add `-- Converted from: <filename>` as the first line
- Preserve original comments
- Add `-- TODO:` for anything requiring manual review
"""


class HDLConverter(BaseConverter):
    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")
        notes: list[str] = []

        # Find the `USE ${db_var};` to determine which dataset this DDL targets
        use_match = re.search(r'USE\s+\$\{(\w+)\};?', content)
        if use_match:
            db_var = use_match.group(1)
            dataset = self.dataset_map.get(db_var, db_var)
        else:
            db_var, dataset = None, None
            notes.append("No USE statement found — dataset must be set manually")

        # Find the primary table name from CREATE [EXTERNAL] TABLE <name>
        table_match = re.search(
            r'CREATE\s+(?:EXTERNAL\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)',
            content,
            re.IGNORECASE,
        )
        table_name = table_match.group(1) if table_match else source.stem

        if dataset:
            target_ref = f"`{self.gcp_project}.{dataset}.{table_name}`"
        else:
            target_ref = f"`{self.gcp_project}.UNKNOWN_DATASET.{table_name}`"

        # Strip the USE line — BigQuery uses fully-qualified names instead
        processed = re.sub(r'USE\s+\$\{\w+\};?\s*\n?', '', content)

        # Replace any ${db_var}.table references inside the body too
        def replace_db_table(m: re.Match) -> str:
            ref_db = m.group(1)
            ref_table = m.group(2)
            ref_dataset = self.dataset_map.get(ref_db)
            if ref_dataset:
                return f"`{self.gcp_project}.{ref_dataset}.{ref_table}`"
            return m.group(0)
        processed = re.sub(r'\$\{(\w+)\}\.(\w+)', replace_db_table, processed)

        # Replace HDFS paths in single-quoted strings
        def replace_path(m: re.Match) -> str:
            return f"'{self.map_path(m.group(1))}'"
        processed = re.sub(r"'(/corp/[^']+)'", replace_path, processed)

        user_message = (
            f"Filename: {source.name}\n"
            f"TARGET_TABLE_REF: {target_ref}\n"
            f"GCP_PROJECT: {self.gcp_project}\n"
            f"DATASET: {dataset or 'UNKNOWN_DATASET'}\n"
            f"(Use TARGET_TABLE_REF verbatim wherever the table is named.)\n\n"
            f"{processed}"
        )
        converted = self.llm.convert(SYSTEM_PROMPT, user_message)

        needs_review = "TODO:" in converted
        if needs_review:
            notes.append("Contains TODO items requiring manual review")
        return ConversionResult(content=converted, notes=notes, needs_review=needs_review)
