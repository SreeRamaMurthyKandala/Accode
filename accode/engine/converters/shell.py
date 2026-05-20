"""Shell trigger scripts (.sh/.ksh) → GCP-compatible bash scripts."""
import re
from pathlib import Path
from .base import BaseConverter, ConversionResult

SYSTEM_PROMPT = """\
You are a senior DevOps engineer migrating Hive/YARN shell scripts to run on GCP
using the `bq` CLI and `gsutil` instead of HDFS/YARN/Hive.

Convert the ksh/bash shell script following these rules.

## Shebang
`#!/bin/ksh` or `#!/bin/bash` → `#!/usr/bin/env bash`

## Add GCP env vars block after the system= assignment
The DATASET_ENV_BLOCK placeholder below will be replaced with the actual dataset
variables derived from the project config.

DATASET_ENV_BLOCK

## Variable name renames
Rename Hive DB variables to their GCP dataset equivalents per DATASET_VAR_RENAMES below.
- `${mapred_qname}` → remove entirely (BQ manages its own resources)
- `${HIVE_RUNNER}` → `bq`
- `${HqlDir}/script.hql` → `${SCRIPT_DIR}/script.sql`
- `${HdlDir}/script.hdl` → `${SCRIPT_DIR}/schema.sql`
- HDFS paths → GCS paths (already substituted in pre-processing — keep as-is)

DATASET_VAR_RENAMES

## hive-runner invocations
Replace:
```ksh
${HIVE_RUNNER} \\
  -i ${ConfigDir}/filters.config \\
  -d cs_db=${cs_db} \\
  -d uc_db=${uc_db} \\
  -d trans_dt="${trans_dt}" \\
  -f ${HqlDir}/load.hql
```
With:
```bash
bq query \\
  --use_legacy_sql=false \\
  --project_id="${GCP_PROJECT}" \\
  --parameter="trans_dt::${TRANS_DT}" \\
  "$(cat ${SCRIPT_DIR}/load.sql)"
```
- Remove `-i filters.config` (filters are now baked into the SQL WHERE clause)
- Remove `-d mapred_qname=...` (not applicable)
- Keep `-d` for date/value parameters, converting to `--parameter="name::value"`
- Use `$()` command substitution to read the SQL file

## Count queries (run_hive_count helper)
Replace:
```ksh
src_cnt=$(run_hive_count "SELECT COUNT(*) FROM ${cs_db}.table WHERE trans_dt='${trans_dt}';")
```
With:
```bash
src_cnt=$(bq query \\
  --use_legacy_sql=false \\
  --project_id="${GCP_PROJECT}" \\
  --format=csv \\
  --quiet \\
  "SELECT COUNT(*) FROM \`${GCP_PROJECT}.${CS_DATASET}.table\` WHERE trans_dt='${TRANS_DT}';" \\
  | tail -1)
```

## Notification (Notify function / MailSubject)
Keep the structure. Replace the `Notify` call with:
```bash
echo "$(${logtm}) ${MailSubject}" | mail -s "${MailSubject}" "${DistributionEmail}" 2>/dev/null || true
echo "$(${logtm}) FAILED: ${step} — exiting" >&2
exit 1
```

## Common utils
- `log_msg "INFO" "..."` → keep as-is, it's still valid bash
- `count_check` helper → keep logic, update table references to BQ

## Date variables
- Keep `trans_dt=$(date -d "-1 day" +"%Y-%m-%d")` — valid bash
- Rename to uppercase: `TRANS_DT` in the GCP script for clarity

## Output format
- Return ONLY the converted bash script — no markdown fences, no explanation
- Add `# Converted from: <filename>` as the first comment line
- Preserve the step structure and all step comments
- Add `# TODO:` for anything requiring manual review or testing
"""


class ShellConverter(BaseConverter):
    def _build_prompt(self) -> str:
        """Inject dataset env block and variable rename rules from config."""
        unique_datasets = sorted(set(self.dataset_map.values()))
        env_lines = ["# GCP configuration — set these in your environment or CI/CD system",
                     'GCP_PROJECT="${GCP_PROJECT:-my-gcp-project}"',
                     'GCS_BUCKET="${GCS_BUCKET:-gs://my-bucket}"']
        rename_lines: list[str] = []
        seen: set[str] = set()
        for hive_var, dataset in self.dataset_map.items():
            env_var = hive_var.upper() + "_DATASET"
            if env_var not in seen:
                seen.add(env_var)
                env_lines.append(f'{env_var}="${{{env_var}:-{dataset}}}"')
            rename_lines.append(f"- `${{{hive_var}}}` → `${{{env_var}}}`")

        env_block = "```bash\n" + "\n".join(env_lines) + "\n```"
        rename_block = "\n".join(rename_lines) if rename_lines else "- (no dataset variables in config)"
        return (SYSTEM_PROMPT
                .replace("DATASET_ENV_BLOCK", env_block)
                .replace("DATASET_VAR_RENAMES", rename_block))

    def convert(self, source: Path) -> ConversionResult:
        content = source.read_text(errors="replace")

        hdfs_pattern = self._hdfs_path_pattern()
        def replace_hdfs(m: re.Match) -> str:
            return self.map_path(m.group(0))
        processed = re.sub(hdfs_pattern, replace_hdfs, content) if hdfs_pattern else content

        prompt = self._build_prompt()
        converted = self.llm.convert(prompt, f"Filename: {source.name}\n\n{processed}")

        needs_review = "TODO:" in converted
        return ConversionResult(
            content=converted,
            notes=["Contains TODO items requiring manual review"] if needs_review else [],
            needs_review=needs_review,
        )
