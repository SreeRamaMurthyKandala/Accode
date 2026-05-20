"""edit_file — replace an exact string inside an existing file."""
from __future__ import annotations

from accode.agent.tooling import PERMISSION_ASK, Tool, ToolResult
from accode.context import Context


def _handler(inp: dict, ctx: Context) -> ToolResult:
    path = ctx.resolve(inp["path"])
    if not path.exists():
        return ToolResult(f"File not found: {path}", is_error=True)
    old = inp.get("old_string", "")
    new = inp.get("new_string", "")
    if old == "":
        return ToolResult("old_string must not be empty.", is_error=True)
    replace_all = bool(inp.get("replace_all", False))
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return ToolResult(f"Could not read {path}: {exc}", is_error=True)

    count = text.count(old)
    if count == 0:
        return ToolResult("old_string not found in the file.", is_error=True)
    if count > 1 and not replace_all:
        return ToolResult(
            f"old_string is not unique ({count} matches). Add surrounding "
            "context to make it unique, or set replace_all=true.",
            is_error=True,
        )
    new_text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    try:
        path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return ToolResult(f"Could not write {path}: {exc}", is_error=True)
    n = count if replace_all else 1
    return ToolResult(f"Edited {path} — {n} replacement(s).")


TOOL = Tool(
    name="edit_file",
    description=(
        "Replace an exact string in an existing file. old_string must match "
        "the file content exactly (including whitespace) and must be unique "
        "unless replace_all is true. Read the file first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path, absolute or relative to the working directory."},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence (default false)."},
        },
        "required": ["path", "old_string", "new_string"],
    },
    handler=_handler,
    permission=PERMISSION_ASK,
)
