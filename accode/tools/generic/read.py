"""read_file — read a UTF-8 text file with line numbers."""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context

_DEFAULT_LIMIT = 1500


def _handler(inp: dict, ctx: Context) -> ToolResult:
    path = ctx.resolve(inp["path"])
    if not path.exists():
        return ToolResult(f"File not found: {path}", is_error=True)
    if path.is_dir():
        return ToolResult(f"Path is a directory, not a file: {path}", is_error=True)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(f"Could not read {path}: {exc}", is_error=True)

    lines = text.splitlines()
    offset = max(0, int(inp.get("offset", 0)))
    limit = int(inp.get("limit", _DEFAULT_LIMIT))
    window = lines[offset:offset + limit]
    if not window:
        return ToolResult(f"(no lines at offset {offset}; file has {len(lines)} lines)")

    body = "\n".join(f"{offset + i + 1:6d}\t{ln}" for i, ln in enumerate(window))
    shown_end = offset + len(window)
    if shown_end < len(lines):
        body += f"\n... [{len(lines) - shown_end} more lines — use offset={shown_end} to continue]"
    return ToolResult(body)


TOOL = Tool(
    name="read_file",
    description=(
        "Read a UTF-8 text file from disk. Returns the content with 1-based "
        "line numbers. For large files use offset (0-based start line) and "
        "limit (max lines, default 1500)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, absolute or relative to the working directory."},
            "offset": {"type": "integer", "description": "0-based line number to start reading from."},
            "limit": {"type": "integer", "description": "Maximum number of lines to return."},
        },
        "required": ["path"],
    },
    handler=_handler,
    permission=PERMISSION_ALLOW,
)
