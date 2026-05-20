"""migration_run_tests — run the converted pytest suite.

Wraps the engine TestRunner (the orchestrator's `_run_tests()` stage). Runs
pytest in a subprocess against output_dir/tests and records results in the
run state for migration_report.
"""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context
from accode.engine.test_runner import TestRunner
from accode.state import load_state, save_state


def _handler(inp: dict, ctx: Context) -> ToolResult:
    output_dir = ctx.resolve(inp.get("output_dir") or ctx.cfg["output"]["dir"])
    if not output_dir.exists():
        return ToolResult(
            f"Output directory does not exist: {output_dir}. Run migration_convert first.",
            is_error=True,
        )

    result = TestRunner(output_dir).run()
    if not result.ran:
        return ToolResult(f"Tests did not run: {result.skipped_reason}")

    lines = [
        f"pytest — {output_dir / 'tests'}",
        f"  {result.passed} passed, {result.failed} failed, {result.errors} error(s)",
    ]
    for f in result.failures:
        first = f.error_excerpt.splitlines()[0][:160] if f.error_excerpt else ""
        lines.append(f"  FAIL  {f.rel_path} — {first}")

    state = load_state(output_dir)
    if state:
        state["tests"] = {
            "ran": True,
            "passed": result.passed,
            "failed": result.failed,
            "errors": result.errors,
            "failures": [[f.rel_path, f.error_excerpt] for f in result.failures],
        }
        save_state(output_dir, state)

    if result.failures:
        lines.append(
            "Repair each failing test with migration_fix(converted_file=<rel>, "
            'error_type="PYTEST"), then re-run this tool. Cap at 3 rounds.'
        )
    return ToolResult("\n".join(lines))


TOOL = Tool(
    name="migration_run_tests",
    description=(
        "STAGE 6 of the Hive->GCP migration. Run pytest on the converted test "
        "suite (output_dir/tests). PRECONDITION: migration_convert must have "
        'run. Fix failures with migration_fix error_type="PYTEST". Only run '
        "this stage if the user asked for the converted code to be tested."
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
