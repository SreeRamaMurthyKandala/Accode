"""Hive filter injection configs (.config with hivevar) → BigQuery WHERE clause docs.

In Hive, these files are passed to hive-runner with `-i filters.config` and inject
`${hivevar:name}` variables as WHERE clause fragments into the HQL at runtime.

In BigQuery, there is no equivalent injection mechanism. The approach is:
  1. Bake the standard filters directly into the converted SQL WHERE clause.
  2. Generate a documented reference file explaining what each filter does.
"""
import re
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are migrating a Hive filter injection config file to BigQuery.

In Hive, this file is passed to hive-runner with `-i filters.config` to inject
`${hivevar:name}` variables as WHERE clause fragments at runtime.

In BigQuery there is no equivalent. Convert this file into TWO sections:

## Section 1: BigQuery WHERE clause fragments
For each `set hivevar:name=<SQL fragment>;` line, extract the SQL fragment and
present it ready to paste into a BigQuery WHERE clause:

```sql
-- === Filters for <job_name> ===
-- Paste these into the WHERE clause of the converted BigQuery SQL:

  AND s.record_type <> 'TEST'                -- filter_excl_test_type
  AND s.record_type <> 'INTERNAL'            -- filter_excl_internal_type
  -- ... etc
```

## Section 2: Migration note
Explain that:
- The original Hive SQL used ${hivevar:filter_name} placeholders injected at runtime
- In the converted BigQuery SQL, these conditions have been baked directly into the WHERE clause
- If runtime filter toggling is needed, implement it via parameterized queries or view predicates

## Output format
- Return a bash comment file (lines starting with #) documenting the migration
- Include the ready-to-use SQL fragments in a clearly marked block
- Add `# Converted from: <filename>` at the top
- No markdown fences in the output
"""


class FiltersConfigConverter(BaseConverter):
    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")

        # Parse out hivevar assignments for the notes
        filters: list[tuple[str, str]] = []
        for m in re.finditer(r'set\s+hivevar:(\w+)\s*=\s*(.+?);', content, re.DOTALL):
            filters.append((m.group(1), m.group(2).strip()))

        converted = self.llm.convert(SYSTEM_PROMPT, f"Filename: {source.name}\n\n{content}")

        notes = [f"Defines {len(filters)} injectable filters: {', '.join(n for n, _ in filters)}"]
        notes.append("Filters baked into converted SQL WHERE clause — see this file for reference")
        return ConversionResult(content=converted, notes=notes, needs_review=False)
