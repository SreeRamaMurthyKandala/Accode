"""migration_discovery — scan a Hive repo and classify every file.

Wraps the engine scanner (the orchestrator's `scan()` stage). Read-only: it
inspects the repo and reports what a migration would convert, writing nothing.
"""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context
from accode.engine.scanner import FileType, scan

_FILE_LIST_LIMIT = 200


def _handler(inp: dict, ctx: Context) -> ToolResult:
    repo_path = ctx.resolve(inp["repo_path"])
    if not repo_path.exists() or not repo_path.is_dir():
        return ToolResult(f"Repo path not found or not a directory: {repo_path}", is_error=True)

    cfg = ctx.cfg
    files = scan(
        str(repo_path),
        dag_xml_dirs=cfg.get("dag_xml_dirs"),
        test_dirs=cfg.get("test_dirs"),
    )
    targets = [f for f in files if f.file_type != FileType.SKIP]
    skipped = [f for f in files if f.file_type == FileType.SKIP]

    by_type: dict[str, int] = {}
    for f in targets:
        by_type[f.file_type.value] = by_type.get(f.file_type.value, 0) + 1

    lines = [
        f"Discovery — {repo_path}",
        f"  {len(targets)} migration target(s), {len(skipped)} non-target file(s) skipped.",
        "  Breakdown by type:",
    ]
    for ftype, count in sorted(by_type.items()):
        lines.append(f"    {ftype:16s} {count}")
    if not targets:
        lines.append("  (nothing to migrate)")
        return ToolResult("\n".join(lines))

    lines.append("  Target files:")
    for f in targets[:_FILE_LIST_LIMIT]:
        lines.append(f"    [{f.file_type.value}] {f.rel_path}")
    if len(targets) > _FILE_LIST_LIMIT:
        lines.append(f"    ... [{len(targets) - _FILE_LIST_LIMIT} more]")
    return ToolResult("\n".join(lines))


TOOL = Tool(
    name="migration_discovery",
    description=(
        "STAGE 1 of the Hive->GCP migration. Scan a Hive/Hadoop repository and "
        "classify every file (HQL, HDL, shell, PySpark, config, DAG-XML, "
        "tests). Read-only — reports what a migration would convert. Run this "
        "first to understand a repo, or on its own if the user only wants a "
        "discovery report."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "Path to the Hive repository root."},
        },
        "required": ["repo_path"],
    },
    handler=_handler,
    permission=PERMISSION_ALLOW,
)
