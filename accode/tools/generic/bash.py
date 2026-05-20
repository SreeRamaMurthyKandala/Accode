"""run_bash — run a shell command in the working directory."""
from __future__ import annotations

import subprocess

from accode.agent.tooling import PERMISSION_ASK, Tool, ToolResult
from accode.context import Context

_OUTPUT_CAP = 20_000


def _handler(inp: dict, ctx: Context) -> ToolResult:
    command = inp.get("command", "")
    if not command.strip():
        return ToolResult("command is required.", is_error=True)
    timeout = int(inp.get("timeout", 120))
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=str(ctx.cwd),
        )
    except subprocess.TimeoutExpired:
        return ToolResult(f"Command timed out after {timeout}s.", is_error=True)
    except OSError as exc:
        return ToolResult(f"Could not run command: {exc}", is_error=True)

    output = (proc.stdout or "") + (proc.stderr or "")
    if len(output) > _OUTPUT_CAP:
        output = output[:_OUTPUT_CAP] + f"\n... [truncated {len(output) - _OUTPUT_CAP} chars]"
    body = f"exit code: {proc.returncode}\n{output or '(no output)'}"
    return ToolResult(body, is_error=proc.returncode != 0)


TOOL = Tool(
    name="run_bash",
    description=(
        "Run a shell command in the working directory and return its combined "
        "stdout/stderr plus exit code. Uses the system shell."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute."},
            "timeout": {"type": "integer", "description": "Timeout in seconds (default 120)."},
        },
        "required": ["command"],
    },
    handler=_handler,
    permission=PERMISSION_ASK,
)
