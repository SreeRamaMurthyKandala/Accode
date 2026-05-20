"""migration_bq_validate — BigQuery dry-run validation of converted SQL.

Wraps the engine validator's dry-run (`validate()`) — the orchestrator's
`validate_only()` stage. No data is scanned and no cost is incurred. Updates
per-file validation status in the run state for migration_report.
"""
from __future__ import annotations

from pathlib import Path

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context
from accode.engine.validator import BQValidator
from accode.state import load_state, save_state


def _clean_bq_error(err: str) -> str:
    """Strip the '400 POST https://...: ' HTTP boilerplate BigQuery prepends."""
    if ": " in err:
        parts = err.split(": ", 2)
        if len(parts) >= 3 and parts[0].startswith(("400", "403", "404")):
            return parts[2]
    return err


def _rel_or_none(output_path: str | None, output_dir: Path) -> str | None:
    if not output_path:
        return None
    try:
        return str(Path(output_path).relative_to(output_dir))
    except ValueError:
        return None


def _handler(inp: dict, ctx: Context) -> ToolResult:
    cfg = ctx.cfg
    output_dir = ctx.resolve(inp.get("output_dir") or cfg["output"]["dir"])
    if not output_dir.exists():
        return ToolResult(
            f"Output directory does not exist: {output_dir}. Run migration_convert first.",
            is_error=True,
        )

    validator = BQValidator(cfg)
    if not validator.available:
        return ToolResult(
            f"BigQuery client unavailable: {validator._unavailable_reason}. "
            "Configure gcp.project and credentials, or skip the BQ stages.",
            is_error=True,
        )

    # which .sql files to validate
    only = inp.get("files")
    if only:
        sql_files: list[Path] = []
        for rel in only:
            p = output_dir / rel
            if p.suffix.lower() != ".sql":
                p = p.with_suffix(".sql")
            if not p.exists():
                return ToolResult(f"SQL file not found: {rel}", is_error=True)
            sql_files.append(p)
    else:
        sql_files = sorted(
            p for p in output_dir.rglob("*.sql")
            if "dags" not in p.relative_to(output_dir).parts
        )
    if not sql_files:
        return ToolResult(f"No .sql files found under {output_dir}.", is_error=True)

    passed = 0
    failures: list[tuple[str, str]] = []
    lines = [f"BigQuery dry-run — {len(sql_files)} file(s):"]
    for p in sql_files:
        rel = str(p.relative_to(output_dir))
        result = validator.validate(p.read_text(encoding="utf-8"))
        if result.success:
            passed += 1
            lines.append(f"  PASS  {rel}  ({result.bytes_processed:,} bytes)")
        else:
            err = _clean_bq_error(result.error or "")
            failures.append((rel, err))
            lines.append(f"  FAIL  {rel} — {err.splitlines()[0][:160]}")

    # full-error log
    if failures:
        blocks = [f"BigQuery dry-run validation — {len(failures)} failure(s)", "=" * 64, ""]
        for rel, err in failures:
            blocks += [f"FILE: {rel}", "-" * 64, err, ""]
        try:
            (output_dir / "VALIDATION_ERRORS.log").write_text("\n".join(blocks), encoding="utf-8")
        except OSError:
            pass

    # update run state for migration_report
    state = load_state(output_dir)
    if state:
        state["bq"] = {"passed": passed, "failures": [list(f) for f in failures]}
        failed_rels = {rel for rel, _ in failures}
        validated_rels = {str(p.relative_to(output_dir)) for p in sql_files}
        for entry in state.get("files", []):
            out_rel = _rel_or_none(entry.get("output_path"), output_dir)
            if out_rel in validated_rels:
                entry["validation_passed"] = out_rel not in failed_rels
                entry["validation_error"] = next(
                    (err for rel, err in failures if rel == out_rel), None
                )
        save_state(output_dir, state)

    lines.append(f"\nResult: {passed} passed, {len(failures)} failed.")
    if failures:
        lines.append(
            "Repair each failure with migration_fix(converted_file=<rel>, "
            'error_type="BQ_DRY_RUN"), then re-run this tool. Cap at 3 rounds.'
        )
        lines.append(f"Full errors written to {output_dir / 'VALIDATION_ERRORS.log'}")
    return ToolResult("\n".join(lines))


TOOL = Tool(
    name="migration_bq_validate",
    description=(
        "STAGE 5 of the Hive->GCP migration. Run a BigQuery dry-run on every "
        "converted .sql file — validates syntax and schema, scans no data, "
        "costs nothing. Run migration_bq_setup first so referenced tables "
        "exist. Requires GCP credentials. PRECONDITION: migration_convert must "
        'have run. Fix failures with migration_fix error_type="BQ_DRY_RUN".'
    ),
    input_schema={
        "type": "object",
        "properties": {
            "output_dir": {"type": "string", "description": "Migration output directory (default: config output.dir)."},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional output-relative .sql paths to validate. Omit to validate all.",
            },
        },
        "required": [],
    },
    handler=_handler,
    permission=PERMISSION_ALLOW,
)
