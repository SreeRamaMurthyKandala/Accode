"""migration_fix — repair one converted file using an error message.

Wraps the engine FixAgent (the orchestrator's `FixAgent.fix_file()`). It is
ATOMIC: one file, one error, one repair pass. The agent loop drives the
round-based retry that the orchestrator's `_run_fix_rounds_*` methods did —
run a validation tool, fix each failure, re-run the validation tool, capped
at three rounds.
"""
from __future__ import annotations

from pathlib import Path

from accode.agent.tooling import PERMISSION_ASK, Tool, ToolResult
from accode.context import Context
from accode.engine.fix_agent import FixAgent
from accode.engine.llm import LLMClient
from accode.paths import lookup_source
from accode.state import load_state

_VALID_TYPES = {"SYNTAX", "BQ_DRY_RUN", "PYTEST", "TODO_REVIEW"}


def _handler(inp: dict, ctx: Context) -> ToolResult:
    cfg = ctx.cfg
    output_dir = ctx.resolve(inp.get("output_dir") or cfg["output"]["dir"])

    error_type = str(inp.get("error_type", "")).upper()
    if error_type not in _VALID_TYPES:
        return ToolResult(f"error_type must be one of {sorted(_VALID_TYPES)}.", is_error=True)
    error_message = str(inp.get("error_message", "")).strip()
    if not error_message:
        return ToolResult("error_message is required.", is_error=True)

    converted_arg = inp.get("converted_file")
    if not converted_arg:
        return ToolResult("converted_file is required.", is_error=True)
    converted = Path(converted_arg)
    if not converted.is_absolute():
        converted = output_dir / converted_arg
    converted = converted.resolve()
    if not converted.exists():
        return ToolResult(f"Converted file not found: {converted}", is_error=True)

    # locate the original Hive source so the repair sees the true intent
    state = load_state(output_dir)
    repo_path = inp.get("repo_path") or (state or {}).get("repo_path")
    source = None
    if repo_path:
        source = lookup_source(converted, output_dir, Path(repo_path))
    source = source or Path("/__no_source__")

    try:
        llm = LLMClient(cfg)
    except ValueError as exc:
        return ToolResult(f"Repair LLM unavailable: {exc}", is_error=True)

    result = FixAgent(cfg, llm).fix_file(
        source_original=source,
        converted_path=converted,
        error_message=error_message,
        error_type=error_type,
    )
    if result.fixed:
        src_note = "" if source.exists() else " (original source not found — repair used the error only)"
        return ToolResult(
            f"Repaired {converted} for {error_type}{src_note}. "
            "Re-run the matching validation tool to confirm."
        )
    return ToolResult(
        f"Repair did not apply: {'; '.join(result.notes) or 'unknown reason'}", is_error=True
    )


TOOL = Tool(
    name="migration_fix",
    description=(
        "Repair ONE converted file given an error. Wraps the second-pass fix "
        "agent: it sees the original Hive source, the broken converted file "
        "and the error, then rewrites the converted file in place. error_type "
        "must be SYNTAX, BQ_DRY_RUN, PYTEST or TODO_REVIEW. After fixing, "
        "re-run the validation tool that produced the error. Keep each fix "
        "loop to its own error class and cap it at 3 rounds — stop early if "
        "the failure count stops dropping."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "converted_file": {"type": "string", "description": "Converted file to repair — output-relative or absolute path."},
            "error_message": {"type": "string", "description": "The exact error text from the validation tool."},
            "error_type": {
                "type": "string",
                "enum": ["SYNTAX", "BQ_DRY_RUN", "PYTEST", "TODO_REVIEW"],
                "description": "Error class being repaired.",
            },
            "output_dir": {"type": "string", "description": "Migration output directory (default: config output.dir)."},
            "repo_path": {"type": "string", "description": "Repo root, to locate the original source (default: from run state)."},
        },
        "required": ["converted_file", "error_message", "error_type"],
    },
    handler=_handler,
    permission=PERMISSION_ASK,
)
