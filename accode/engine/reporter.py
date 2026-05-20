"""Generate the MIGRATION_REPORT.md after conversion completes."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileResult:
    rel_path: str
    file_type: str
    status: str                         # "converted" | "review" | "skipped" | "error"
    notes: list[str] = field(default_factory=list)
    validation_passed: bool | None = None
    validation_error: str | None = None


class Reporter:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg

    def generate(self, results: list[FileResult], output_dir: Path) -> Path:
        converted = [r for r in results if r.status == "converted"]
        review = [r for r in results if r.status == "review"]
        errors = [r for r in results if r.status == "error"]
        skipped = [r for r in results if r.status == "skipped"]
        validated = [r for r in converted if r.validation_passed is True]
        val_failed = [r for r in converted if r.validation_passed is False]

        lines: list[str] = [
            "# Hive → GCP Migration Report\n",
            "## Summary\n",
            "| Metric | Count |",
            "|--------|-------|",
            f"| Files converted | {len(converted) + len(review)} |",
            f"| BQ dry-run passed | {len(validated)} |",
            f"| BQ dry-run failed | {len(val_failed)} |",
            f"| Needs human review | {len(review)} |",
            f"| Conversion errors | {len(errors)} |",
            f"| Skipped (non-target) | {len(skipped)} |",
            "",
        ]

        if converted:
            lines.append("## Converted Files\n")
            for r in converted:
                if r.validation_passed is True:
                    icon = "✓ BQ validated"
                elif r.validation_passed is False:
                    icon = "✗ BQ validation failed"
                else:
                    icon = "– validation skipped"
                lines.append(f"- `{r.rel_path}` [{r.file_type}] {icon}")
                for note in r.notes:
                    lines.append(f"  - {note}")
            lines.append("")

        if review:
            lines.append("## Needs Human Review\n")
            lines.append("These files were converted but contain TODO items or constructs "
                         "Claude could not automatically translate.\n")
            for r in review:
                lines.append(f"- `{r.rel_path}` [{r.file_type}]")
                for note in r.notes:
                    lines.append(f"  - {note}")
            lines.append("")

        if val_failed:
            lines.append("## BigQuery Dry-Run Failures\n")
            lines.append("Fix these before running in production.\n")
            for r in val_failed:
                lines.append(f"- `{r.rel_path}`")
                lines.append(f"  - Error: `{r.validation_error}`")
            lines.append("")

        if errors:
            lines.append("## Conversion Errors\n")
            for r in errors:
                lines.append(f"- `{r.rel_path}`")
                for note in r.notes:
                    lines.append(f"  - {note}")
            lines.append("")

        lines += [
            "## Next Steps\n",
            "1. Search migrated files for `# TODO:` and `-- TODO:` markers and resolve them.",
            "2. Update `config.yaml` `dataset_map` and `path_map` to match your actual GCP project.",
            "3. Create BigQuery datasets and run the converted `.sql` DDL files to create tables.",
            "4. Upload Airflow DAGs from `migrated/dags/` to your Cloud Composer environment.",
            "5. Run `bq query --dry_run` on any SQL that failed BQ validation above.",
            "6. Run the converted test suite: `pytest migrated/tests/ -v`",
            "7. Execute one pipeline end-to-end with a small date range before full production.",
            "",
        ]

        report_path = output_dir / "MIGRATION_REPORT.md"
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path
