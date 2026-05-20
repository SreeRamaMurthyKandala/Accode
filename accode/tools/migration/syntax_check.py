"""migration_syntax_check — static syntax checks on converted shell/Python.

Wraps the engine SyntaxChecker (the orchestrator's `_run_syntax_checks()`):
`bash -n` for .sh files, Python `compile()` for .py files. Zero cost, no
execution — catches stray markdown fences, unterminated strings, etc.
"""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context
from accode.engine.syntax_checker import SyntaxChecker


def _handler(inp: dict, ctx: Context) -> ToolResult:
    output_dir = ctx.resolve(inp.get("output_dir") or ctx.cfg["output"]["dir"])
    if not output_dir.exists():
        return ToolResult(
            f"Output directory does not exist: {output_dir}. Run migration_convert first.",
            is_error=True,
        )

    result = SyntaxChecker(output_dir).check_all()
    if not result.ran:
        return ToolResult("Syntax check did not run: " + "; ".join(result.notes or ["unknown"]))

    lines = [
        f"Syntax check — {output_dir}",
        f"  shell : {result.sh_checked - result.sh_failed} ok / {result.sh_failed} failed"
        f" (of {result.sh_checked} checked)",
        f"  python: {result.py_checked - result.py_failed} ok / {result.py_failed} failed"
        f" (of {result.py_checked} checked)",
    ]
    for note in result.notes:
        lines.append(f"  note: {note}")
    if result.failures:
        lines.append("  Failures:")
        for f in result.failures:
            lines.append(f"    [{f.file_type}] {f.rel_path} — {f.error}")
        lines.append(
            "Repair each failure with migration_fix(converted_file=<rel_path>, "
            'error_type="SYNTAX"), then re-run this tool. Cap at 3 rounds.'
        )
    else:
        lines.append("  All converted shell/Python files parse cleanly.")
    return ToolResult("\n".join(lines))


TOOL = Tool(
    name="migration_syntax_check",
    description=(
        "STAGE 3 of the Hive->GCP migration. Static syntax checks on converted "
        "files: `bash -n` for .sh and Python compile() for .py. Zero cost, no "
        "execution. PRECONDITION: migration_convert must have produced the "
        'output directory. Fix failures with migration_fix error_type="SYNTAX".'
    ),
    input_schema={
        "type": "object",
        "properties": {
            "output_dir": {"type": "string", "description": "Migration output directory (default: config output.dir)."},
        },
        "required": [],
    },
    handler=_handler,
    permission=PERMISSION_ALLOW,
)
