"""PySpark test files → BigQuery pytest files."""
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are migrating PySpark-based unit tests to pytest tests that use the
Google BigQuery Python client library instead of SparkSession.

Convert the test file following these rules.

## conftest.py handling
If this is a conftest.py:
- Replace `SparkSession` fixture with a `bigquery.Client` fixture
- Replace DataFrame fixtures with lists of dicts or pandas DataFrames
- Replace PySpark filter helper functions with pure Python / pandas equivalents
- Keep pytest fixture decorators

## Test files
- Keep the class structure and test method names
- Replace `df.collect()` result access with list-of-dict access
- Replace `df.count()` with `len(rows)`
- Replace `{r["col"] for r in df.collect()}` → `{r["col"] for r in rows}`
- Replace `df.filter(F.col("x") == y)` → `[r for r in rows if r["x"] == y]`
- Replace `df.groupBy(...).agg(...)` → equivalent Python logic or pandas

## SparkSession imports → remove
```python
# Remove
from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import ...

# Add
import pytest
from google.cloud import bigquery
import os
```

## DataFrame operations → Python equivalents
- `df.filter(F.col("col") == val)` → `[r for r in rows if r["col"] == val]`
- `df.filter(F.col("col") > val)` → `[r for r in rows if r["col"] > val]`
- `df.groupBy("a", "b").agg(F.count("*").alias("cnt"))` →
  ```python
  from collections import Counter
  counts = Counter((r["a"], r["b"]) for r in rows)
  ```
- `df.count()` → `len(rows)`
- `df.collect()` → already a list in the new form
- `df.unionByName(other)` → `rows + other_rows`
- `df.dropDuplicates(["a", "b"])` →
  ```python
  seen, deduped = set(), []
  for r in rows:
      key = (r["a"], r["b"])
      if key not in seen:
          seen.add(key)
          deduped.append(r)
  ```

## conftest fixture for apply_ingest_filters
Replace the PySpark version with a pure Python function that applies
the same filter logic using list comprehensions:
```python
from decimal import Decimal

def apply_ingest_filters(rows: list[dict], min_declared_value: Decimal = Decimal("10.00")) -> list[dict]:
    return [
        r for r in rows
        if r.get("record_type") not in ("TEST", "INTERNAL")
        and r.get("shipment_status_code") in ("BOOKED", "IN_TRANSIT", "DELIVERED")
        and (r.get("declared_value") or Decimal("0")) >= min_declared_value
        and r.get("carrier_id") not in (None, "")
    ]
```

## Test data fixtures
Replace Spark DataFrames with plain Python lists of dicts:
```python
@pytest.fixture
def source_shipments_rows() -> list[dict]:
    return [
        {"shipment_id": "SHIP00001", "record_type": "STANDARD", ...},
        ...
    ]
```

## BQ integration tests (optional)
If the test does actual BQ queries, add a `bq_client` fixture:
```python
@pytest.fixture(scope="session")
def bq_client():
    return bigquery.Client(project=os.environ.get("GCP_PROJECT", "my-project"))
```

## Output format
- Return ONLY the converted Python test file — no markdown fences, no explanation
- Add `# Converted from: <filename>` as the first comment line
- Keep all test docstrings
- Add `# TODO:` for anything requiring manual review
"""


class TestFileConverter(BaseConverter):
    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")

        converted = self.llm.convert(SYSTEM_PROMPT, f"Filename: {source.name}\n\n{content}")

        needs_review = "TODO:" in converted or "pyspark" in converted.lower()
        notes = ["Tests converted from PySpark to pure Python — validate filter logic matches original HQL"]
        if needs_review:
            notes.append("Contains TODO items requiring manual review")
        return ConversionResult(content=converted, notes=notes, needs_review=needs_review)
