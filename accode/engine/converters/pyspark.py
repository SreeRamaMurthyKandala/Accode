"""PySpark scripts → BigQuery Python client scripts."""
import re
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are an expert Python engineer migrating PySpark (with Hive support) scripts
to use the Google BigQuery Python client library.

Convert the PySpark script to use `google-cloud-bigquery` and `google-cloud-storage`
following these rules exactly.

## SparkSession → BigQuery client
Remove:
```python
from pyspark.sql import SparkSession
spark = SparkSession.builder.appName(job_name) \\
    .config("spark.yarn.queue", queue) \\
    .enableHiveSupport() \\
    .getOrCreate()
```
Replace with:
```python
import os
from google.cloud import bigquery

GCP_PROJECT = os.environ.get("GCP_PROJECT", "my-gcp-project")
client = bigquery.Client(project=GCP_PROJECT)
```

## spark.sql() → client.query()
```python
# Old
df = spark.sql(f"SELECT ... FROM {hive_db}.table WHERE ...")

# New — qualify table as project.dataset.table
rows = list(client.query(
    f"SELECT ... FROM `{GCP_PROJECT}.{dataset}.table` WHERE ..."
).result())
```

## df.collect() → list(query.result())
Already handled above — `query.result()` returns Row-like objects with attribute access.

## df.write.insertInto(table) → streaming insert or load job
For small audit/log rows (single row inserts):
```python
errors = client.insert_rows_json(
    f"{GCP_PROJECT}.{dataset}.table_name",
    [{"col": value, ...}]
)
if errors:
    raise RuntimeError(f"BigQuery insert errors: {errors}")
```
For larger DataFrames, use a load job with a pandas DataFrame:
```python
import pandas as pd
pandas_df = pd.DataFrame(rows, columns=[...])
job = client.load_table_from_dataframe(pandas_df, f"{GCP_PROJECT}.{dataset}.table")
job.result()
```

## spark.createDataFrame(rows, schema) → plain Python list of dicts
When used only to call insertInto, replace with a list of dicts for insert_rows_json.

## StructType / StructField imports → remove
Remove `from pyspark.sql.types import ...` — use Python native types and BigQuery schema.

## HDFS file output → GCS via google-cloud-storage
```python
# Old: open(output_path, "w") where output_path is HDFS
# New:
from google.cloud import storage
gcs = storage.Client()
bucket_name = output_path.split("/")[2]  # gs://bucket/path
blob_path = "/".join(output_path.split("/")[3:])
blob = gcs.bucket(bucket_name).blob(blob_path)
blob.upload_from_string(content, content_type="text/csv")
```

## argparse
Keep all argparse args — just rename Hive-specific ones:
- `--hive_db` → `--bq_dataset`
- `--dim_db` → `--dim_dataset`
- Remove `--queue` (YARN queue, not needed)
- Keep `--job_name`, `--export_year_month`, `--output_path`, etc.

## Hive DB references in f-strings
- `f"{hive_db}.table"` → `f"{GCP_PROJECT}.{bq_dataset}.table"`
- `f"{args.hive_db}.table"` → `f"{GCP_PROJECT}.{args.bq_dataset}.table"`

## Output format
- Return ONLY the converted Python script — no markdown fences, no explanation
- Add `# Converted from: <filename>` as the first comment line
- Keep all docstrings and module-level comments
- Add `# TODO:` for anything requiring manual review
"""


class PySparkConverter(BaseConverter):
    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")

        # Pre-process HDFS paths in string literals (derive pattern from path_map)
        hdfs_pat = self._hdfs_path_pattern(capture=True)
        def replace_path_dq(m: re.Match) -> str:
            return f'"{self.map_path(m.group(1))}"'
        def replace_path_sq(m: re.Match) -> str:
            return f"'{self.map_path(m.group(1))}'"

        if hdfs_pat:
            processed = re.sub(f'"({hdfs_pat[1:-1]}[^"]*)"', replace_path_dq, content)
            processed = re.sub(f"'({hdfs_pat[1:-1]}[^']*)'", replace_path_sq, processed)
        else:
            processed = content

        # Replace Hive DB name strings with dataset names where known
        for hive_db, dataset in self.dataset_map.items():
            processed = processed.replace(f'"{hive_db}"', f'"{dataset}"')
            processed = processed.replace(f"'{hive_db}'", f"'{dataset}'")

        converted = self.llm.convert(SYSTEM_PROMPT, f"Filename: {source.name}\n\n{processed}")

        needs_review = "TODO:" in converted or "pyspark" in converted.lower()
        notes = []
        if needs_review:
            notes.append("Contains TODO items or residual PySpark references — review required")
        return ConversionResult(content=converted, notes=notes, needs_review=needs_review)
