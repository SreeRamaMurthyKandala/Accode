"""write_file — create a new file or fully overwrite an existing one."""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ASK, Tool, ToolResult
from accode.context import Context


def _handler(inp: dict, ctx: Context) -> ToolResult:
    path = ctx.resolve(inp["path"])
    content = inp.get("content", "")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult(f"Could not write {path}: {exc}", is_error=True)
    return ToolResult(f"Wrote {len(content)} characters to {path}")


TOOL = Tool(
    name="write_file",
    description=(
        "Create a file or completely overwrite an existing one. Parent "
        "directories are created as needed. For small changes to an existing "
        "file prefer edit_file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, absolute or relative to the working directory."},
            "content": {"type": "string", "description": "Full file content to write."},
        },
        "required": ["path", "content"],
    },
    handler=_handler,
    permission=PERMISSION_ASK,
)
