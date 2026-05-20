"""migration_bq_setup — create BigQuery datasets and execute converted DDL.

Wraps the engine validator's `ensure_datasets()` + `execute_ddl()` (the
orchestrator's GCP-setup sub-stage). This is the one migration tool that
creates REAL cloud resources, so it asks for permission before running.
"""
from __future__ import annotations

from pathlib import Path

from accode.agent.tooling import PERMISSION_ASK, Tool, ToolResult
from accode.context import Context
from accode.engine.validator import BQValidator
from accode.state import load_state

_DDL_PREFIXES = (
    "CREATE TABLE", "CREATE OR REPLACE TABLE",
    "CREATE SCHEMA", "CREATE OR REPLACE SCHEMA", "DROP TABLE",
)


def _looks_like_ddl(sql: str) -> bool:
    return sql.lstrip().upper().startswith(_DDL_PREFIXES)


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

    region = cfg.get("gcp", {}).get("region", "US")
    dataset_map = cfg.get("dataset_map", {})
    lines: list[str] = []

    # 1 — datasets
    validator.ensure_datasets(dataset_map, region)
    datasets = sorted(set(dataset_map.values()))
    lines.append(
        f"Ensured {len(datasets)} dataset(s): "
        + (", ".join(datasets) if datasets else "(none configured in dataset_map)")
    )

    # 2 — locate DDL files: prefer recorded state, else detect by SQL content
    ddl_paths: list[Path] = []
    state = load_state(output_dir)
    if state and state.get("files"):
        for entry in state["files"]:
            if entry.get("file_type") == "hdl":
                p = Path(entry["output_path"])
                if p.exists():
                    ddl_paths.append(p)
    if not ddl_paths:
        for p in sorted(output_dir.rglob("*.sql")):
            if "dags" in p.relative_to(output_dir).parts:
                continue
            try:
                if _looks_like_ddl(p.read_text(encoding="utf-8")):
                    ddl_paths.append(p)
            except OSError:
                continue

    # 3 — execute each DDL statement
    ok = fail = 0
    for p in ddl_paths:
        try:
            sql = p.read_text(encoding="utf-8")
        except OSError as exc:
            fail += 1
            lines.append(f"  DDL skipped: {p} — {exc}")
            continue
        result = validator.execute_ddl(sql)
        rel = p.relative_to(output_dir)
        if result.success:
            ok += 1
        else:
            fail += 1
            lines.append(f"  DDL failed: {rel} — {(result.error or '').splitlines()[0][:160]}")

    lines.append(f"Executed DDL: {ok} table(s) created/updated, {fail} failed.")
    if fail:
        lines.append('Repair failing DDL with migration_fix(error_type="BQ_DRY_RUN").')
    return ToolResult("\n".join(lines), is_error=(fail > 0 and ok == 0))


TOOL = Tool(
    name="migration_bq_setup",
    description=(
        "STAGE 4 of the Hive->GCP migration. Create the BigQuery datasets from "
        "dataset_map and execute the converted DDL (HDL) files to provision "
        "tables. CREATES REAL CLOUD RESOURCES. Requires GCP credentials. Run "
        "before migration_bq_validate so dry-run queries can resolve their "
        "tables. PRECONDITION: migration_convert must have run. If the "
        "BigQuery client is unavailable, skip this and migration_bq_validate."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "output_dir": {"type": "string", "description": "Migration output directory (default: config output.dir)."},
        },
        "required": [],
    },
    handler=_handler,
    permission=PERMISSION_ASK,
)
