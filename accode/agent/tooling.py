"""Core tool abstractions: the Tool definition and its result type.

Every Accode capability — generic file ops and migration stages alike — is a
`Tool`. The agentic loop never special-cases a tool; it just passes the
`api_schema()` list to the model and dispatches `tool_use` blocks to handlers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from accode.context import Context

PERMISSION_ALLOW = "allow"   # run without asking
PERMISSION_ASK = "ask"       # prompt the user before running
PERMISSION_DENY = "deny"     # never run


@dataclass
class ToolResult:
    """Outcome of one tool invocation, fed back to the model as a tool_result."""

    content: str
    is_error: bool = False


# A handler takes parsed tool input + the shared Context and returns a ToolResult.
Handler = Callable[[dict[str, Any], Context], ToolResult]


@dataclass
class Tool:
    """A single capability the agent can invoke."""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Handler
    permission: str = PERMISSION_ASK

    def api_schema(self) -> dict[str, Any]:
        """The shape the Anthropic API expects inside its `tools` array."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
