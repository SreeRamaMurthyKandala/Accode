"""search_text — regex-search file contents."""
from __future__ import annotations

import re

from accode.agent.tooling import PERMISSION_ALLOW, Tool, ToolResult
from accode.context import Context

_HIT_LIMIT = 200


def _handler(inp: dict, ctx: Context) -> ToolResult:
    pattern = inp.get("pattern", "")
    if not pattern:
        return ToolResult("pattern is required.", is_error=True)
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return ToolResult(f"Invalid regular expression: {exc}", is_error=True)

    base = ctx.resolve(inp.get("path", "."))
    if not base.exists():
        return ToolResult(f"Path not found: {base}", is_error=True)
    glob = inp.get("glob", "**/*")
    files = [base] if base.is_file() else sorted(base.glob(glob))

    hits: list[str] = []
    for f in files:
        if not f.is_file():
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(content.splitlines(), start=1):
            if rx.search(line):
                hits.append(f"{f}:{n}: {line.strip()[:200]}")
                if len(hits) >= _HIT_LIMIT:
                    hits.append("... [result limit reached — narrow the search]")
                    return ToolResult("\n".join(hits))
    if not hits:
        return ToolResult(f"No matches for /{pattern}/.")
    return ToolResult("\n".join(hits))


TOOL = Tool(
    name="search_text",
    description=(
        "Search file contents with a Python regular expression. Returns "
        "file:line: matches. Restrict scope with path and a glob pattern."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Python regular expression to search for."},
            "path": {"type": "string", "description": "File or directory to search (default: working directory)."},
            "glob": {"type": "string", "description": "Glob filter when path is a directory (default '**/*')."},
        },
        "required": ["pattern"],
    },
    handler=_handler,
    permission=PERMISSION_ALLOW,
)
