"""Directory config files (.config without hivevar) → GCP env config."""
import re
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are migrating a Hive/Hadoop shell config file (key=value pairs sourced by bash scripts)
to a GCP-equivalent environment config file.

Convert following these rules.

## Add GCP block at the top (after comments, before existing vars)
```bash
# GCP settings — override via environment or CI/CD
GCP_PROJECT="${GCP_PROJECT:-my-gcp-project}"
GCS_BUCKET="${GCS_BUCKET:-gs://my-bucket}"
GCP_REGION="${GCP_REGION:-US}"
```

## Database variable renames (rename to UPPERCASE GCP convention)
DATASET_VAR_RENAMES
(values will already be substituted by pre-processing — keep substituted values)

## YARN/MapReduce variables → remove
- `mapred_qname=...` → replace with: `# REMOVED: mapred_qname (YARN queue — not needed in BQ)`

## Hive runner reference
- `HIVE_RUNNER=...` → `BQ_RUNNER=bq`

## HDFS paths → GCS (paths already substituted in pre-processing)
- Paths starting with `gs://` → keep as-is
- `HiveDBPath=...` → `BQ_DATASET_PATH=...` (rename the variable, keep GCS value)

## Local directory variables
Keep these as-is — they describe script-local paths on the runner machine:
- `HomeDir=...`
- `ConfigDir=...`
- `LogDir=...`
- `ScriptDir=...`
- `TmpDir=...`
- `HqlDir=...` → rename to `SqlDir=...`
- `HdlDir=...` → rename to `SchemaDir=...`

## Date variables
Keep all date calculations as-is — standard bash.

## Notification
Keep `DistributionEmail=...` — still valid.

## Output format
- Return ONLY the converted config file (valid bash source syntax)
- Add `# Converted from: <filename>` as the first comment line
- Keep all original comments
- Add `# TODO:` for anything requiring manual review
"""


class ConfigConverter(BaseConverter):
    def _build_prompt(self) -> str:
        rename_lines: list[str] = []
        for hive_var, dataset in self.dataset_map.items():
            env_var = hive_var.upper() + "_DATASET"
            rename_lines.append(f"- `{hive_var}=<value>` → `{env_var}={dataset}`")
        rename_block = "\n".join(rename_lines) if rename_lines else "- (no dataset variables in config)"
        return SYSTEM_PROMPT.replace("DATASET_VAR_RENAMES", rename_block)

    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")

        # Substitute HDFS paths with GCS paths
        hdfs_pattern = self._hdfs_path_pattern()
        def replace_hdfs(m: re.Match) -> str:
            return self.map_path(m.group(0))
        processed = re.sub(hdfs_pattern, replace_hdfs, content) if hdfs_pattern else content

        # Substitute known Hive DB names with BQ dataset names
        for hive_db, dataset in self.dataset_map.items():
            processed = processed.replace(f"={hive_db}", f"={dataset}")
            processed = processed.replace(f"={hive_db}\n", f"={dataset}\n")

        prompt = self._build_prompt()
        converted = self.llm.convert(prompt, f"Filename: {source.name}\n\n{processed}")

        needs_review = "TODO:" in converted
        return ConversionResult(
            content=converted,
            notes=["Contains TODO items requiring manual review"] if needs_review else [],
            needs_review=needs_review,
        )
