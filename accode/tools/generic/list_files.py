"""list_files — glob for files under a directory."""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context

_LIMIT = 400


def _handler(inp: dict, ctx: Context) -> ToolResult:
    base = ctx.resolve(inp.get("path", "."))
    pattern = inp.get("pattern", "*")
    if not base.exists():
        return ToolResult(f"Path not found: {base}", is_error=True)
    if not base.is_dir():
        return ToolResult(f"Path is not a directory: {base}", is_error=True)
    try:
        matches = sorted(str(p) for p in base.glob(pattern) if p.is_file())
    except (ValueError, OSError) as exc:
        return ToolResult(f"Bad glob pattern: {exc}", is_error=True)
    if not matches:
        return ToolResult(f"No files match '{pattern}' under {base}.")
    shown = matches[:_LIMIT]
    body = "\n".join(shown)
    if len(matches) > _LIMIT:
        body += f"\n... [{len(matches) - _LIMIT} more — narrow the pattern]"
    return ToolResult(body)


TOOL = Tool(
    name="list_files",
    description=(
        "List files matching a glob pattern. Supports ** for recursive "
        "matching, e.g. pattern='**/*.py'."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '*.txt' or '**/*.sql'."},
            "path": {"type": "string", "description": "Directory to search in (default: working directory)."},
        },
        "required": ["pattern"],
    },
    handler=_handler,
    permission=PERMISSION_ALLOW,
)
