"""migration_report — render MIGRATION_REPORT.md from the recorded run state.

Wraps the engine Reporter (the orchestrator's `Reporter.generate()` plus the
`_patch_report()` validation patch). It is a pure function of the run state:
conversion results, BigQuery validation, and test results.
"""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context
from accode.engine.reporter import FileResult, Reporter
from accode.state import load_state


def _handler(inp: dict, ctx: Context) -> ToolResult:
    output_dir = ctx.resolve(inp.get("output_dir") or ctx.cfg["output"]["dir"])
    state = load_state(output_dir)
    if not state or not state.get("files"):
        return ToolResult(
            f"No migration run state found in {output_dir}. Run migration_convert first.",
            is_error=True,
        )

    results = [
        FileResult(
            rel_path=e["rel_path"],
            file_type=e["file_type"],
            status=e["status"],
            notes=list(e.get("notes", [])),
            validation_passed=e.get("validation_passed"),
            validation_error=e.get("validation_error"),
        )
        for e in state["files"]
    ]
    report_path = Reporter(ctx.cfg).generate(results, output_dir)

    # the engine Reporter has no test section — append one if tests have run
    tests = state.get("tests")
    if tests and tests.get("ran"):
        text = report_path.read_text(encoding="utf-8")
        section = [
            "\n## Test Results\n",
            f"pytest: **{tests['passed']} passed**, {tests['failed']} failed, "
            f"{tests['errors']} error(s).\n",
        ]
        for rel, excerpt in tests.get("failures", []):
            first = (excerpt or "").splitlines()[0][:200] if excerpt else ""
            section.append(f"- `{rel}`")
            section.append(f"  - `{first}`")
        section.append("")
        block = "\n".join(section)
        if "## Next Steps" in text:
            text = text.replace("## Next Steps", block + "\n## Next Steps")
        else:
            text += "\n" + block
        report_path.write_text(text, encoding="utf-8")

    converted = sum(1 for e in state["files"] if e["status"] in ("converted", "review"))
    bq = state.get("bq")
    bq_line = ""
    if bq:
        bq_line = f"  BigQuery dry-run: {bq['passed']} passed, {len(bq['failures'])} failed.\n"
    return ToolResult(
        f"Wrote migration report -> {report_path}\n"
        f"  {converted} converted file(s) summarised.\n"
        + bq_line
    )


TOOL = Tool(
    name="migration_report",
    description=(
        "FINAL STAGE of the Hive->GCP migration. Render MIGRATION_REPORT.md "
        "from the recorded run state: conversion summary, files needing human "
        "review, BigQuery validation results and test results. PRECONDITION: "
        "migration_convert must have run. Safe to call at any point to get a "
        "snapshot of the current run state."
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
