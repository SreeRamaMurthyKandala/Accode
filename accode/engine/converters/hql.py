"""HiveQL (.hql) → BigQuery Standard SQL converter."""
import re
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are an expert database engineer migrating HiveQL to Google BigQuery Standard SQL.

Convert the HiveQL below to BigQuery SQL following these rules exactly.

## Remove entirely
- All `SET mapreduce.*`, `SET hive.*`, `SET mapred.*` lines
- `USE ${...};` lines (BQ uses fully-qualified `project.dataset.table` names)

## Variable substitution
- `${variable}` runtime variables in the SQL have already been partially resolved.
  Any remaining `${variable}` not resolved by pre-processing → convert to `@variable`
  (BigQuery scripting parameter syntax).
- `${hivevar:filter_name}` injectable WHERE conditions → expand each to its logical
  SQL fragment based on the comment above it in the file, then add:
  `-- FILTER filter_name: <the expanded condition> (was injected at Hive runtime)`

## Function translations (apply all)
- `from_unixtime(unix_timestamp())` → `CURRENT_TIMESTAMP()`
- `unix_timestamp()` → `UNIX_SECONDS(CURRENT_TIMESTAMP())`
- `from_unixtime(expr)` → `TIMESTAMP_SECONDS(expr)`
- `date_format(col, 'yyyy-MM-dd')` → `FORMAT_DATE('%Y-%m-%d', col)`
- `date_add(col, n)` → `DATE_ADD(col, INTERVAL n DAY)`
- `datediff(a, b)` → `DATE_DIFF(a, b, DAY)`
- `nvl(a, b)` → `IFNULL(a, b)`
- `nvl2(a, b, c)` → `IF(a IS NOT NULL, b, c)`
- `concat_ws(sep, a, b)` → `CONCAT(a, sep, b)` (or STRING_AGG for aggregation)
- `regexp_extract(col, pattern, 1)` → `REGEXP_EXTRACT(col, pattern)`
- `split(col, delim)[index]` → `SPLIT(col, delim)[SAFE_OFFSET(index)]`
- `size(array_col)` → `ARRAY_LENGTH(array_col)`
- `collect_set(col)` → `ARRAY_AGG(DISTINCT col IGNORE NULLS)`
- `collect_list(col)` → `ARRAY_AGG(col)`
- `get_json_object(col, '$.key')` → `JSON_VALUE(col, '$.key')`
- `to_date(col)` → `DATE(col)`
- `year(col)` → `EXTRACT(YEAR FROM col)`
- `month(col)` → `EXTRACT(MONTH FROM col)`
- `day(col)` → `EXTRACT(DAY FROM col)`
- `CAST(x AS BIGINT)` → `CAST(x AS INT64)`
- `CAST(x AS FLOAT)` → `CAST(x AS FLOAT64)`
- `CAST(x AS DOUBLE)` → `CAST(x AS FLOAT64)`

## INSERT OVERWRITE
- `INSERT OVERWRITE TABLE project.dataset.table PARTITION (col)` →
  `INSERT INTO project.dataset.table` (BQ overwrites partitions automatically
  when the table is partitioned and PARTITION BY is set on the table)
  Add comment: `-- BQ: partition overwrite handled by table-level PARTITION BY`

## Window functions
- `ROW_NUMBER() OVER (...)`, `RANK()`, `DENSE_RANK()`, `LAG()`, `LEAD()` → keep as-is (BQ supports these)

## Type coercion in INSERT statements
BigQuery is stricter than Hive about type matches. When inserting a value into
a column whose target type differs from the value's natural type, you MUST cast.
Common cases (apply unless you have strong evidence the target column has a
matching type):

- Inserting `CURRENT_TIMESTAMP()` / `CURRENT_DATETIME()` / `CURRENT_DATE()` into
  a column whose name suggests STRING storage (`*_timestamp`, `*_ts`, `*_date`,
  `load_timestamp`, `created_at_str`, etc. that were STRING in the source Hive
  DDL) → wrap with `CAST(... AS STRING)` and add a comment:
  `-- CAST to STRING because target column is STRING in the Hive schema`
- Inserting any TIMESTAMP/DATETIME expression into a known-STRING column → CAST
- Inserting a NUMERIC/INT expression into a known-STRING column → CAST AS STRING
- When unsure of target column type, default to CASTing to STRING for `*_timestamp`,
  `*_dt`, `*_date` columns since Hive commonly stored these as STRING.

## Output format
- Return ONLY the converted BigQuery SQL — no markdown fences, no explanation
- Add `-- Converted from: <filename>` as the first line
- Preserve all original comments
- For anything you cannot confidently translate, add `-- TODO: <explanation>`
"""


class HQLConverter(BaseConverter):
    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")
        notes: list[str] = []
        needs_review = False

        processed, pre_notes = self._preprocess(content, source.name)
        notes.extend(pre_notes)

        converted = self.llm.convert(SYSTEM_PROMPT, f"Filename: {source.name}\n\n{processed}")

        if "TODO:" in converted:
            needs_review = True
            notes.append("Contains TODO items requiring manual review")
        if "${hivevar:" in converted:
            needs_review = True
            notes.append("Unresolved hivevar filter references remain")

        return ConversionResult(content=converted, notes=notes, needs_review=needs_review)

    def _preprocess(self, content: str, filename: str) -> tuple[str, list[str]]:
        notes: list[str] = []

        # Replace ${db_var}.table with fully-qualified BQ reference when db_var is in dataset_map
        def replace_db_table(m: re.Match) -> str:
            db_var = m.group(1)
            table = m.group(2)
            dataset = self.dataset_map.get(db_var)
            if dataset:
                ref = f"`{self.gcp_project}.{dataset}.{table}`"
                notes.append(f"${{{db_var}}}.{table} → {ref}")
                return ref
            return m.group(0)

        content = re.sub(r'\$\{(\w+)\}\.(\w+)', replace_db_table, content)

        # Replace HDFS path strings
        def replace_path(m: re.Match) -> str:
            return f"'{self.map_path(m.group(1))}'"
        content = re.sub(r"'(/corp/[^']+)'", replace_path, content)

        return content, notes
