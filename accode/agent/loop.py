"""The agentic loop — Accode's core control flow.

    user goal -> model picks tool(s) -> run them -> feed results back -> repeat

The loop is tiny and entirely UI-agnostic: it emits structured events to a
sink (see accode/agent/events.py) and asks for permission through the gate.
The stage ordering that used to be hard-coded in the Hive->GCP orchestrator
now lives in the system prompt.
"""
from __future__ import annotations

from typing import Callable, Optional

from accode.agent.events import EventSink
from accode.agent.llm import AgentLLM
from accode.agent.permissions import PermissionGate
from accode.agent.prompt import build_system_prompt
from accode.agent.registry import build_registry
from accode.agent.tooling import ToolResult
from accode.context import Context

_RESULT_CHAR_CAP = 50_000    # final safety net on the size of one tool result
_PREVIEW_CHAR_CAP = 1_500    # how much of a result the UI gets to show


def _truncate(text: str, limit: int = _RESULT_CHAR_CAP) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


def _call_label(tool_name: str, tool_input: dict) -> str:
    """A short, single-line rendering of a tool call."""
    parts = []
    for key, value in tool_input.items():
        s = str(value).replace("\n", " ")
        if len(s) > 60:
            s = s[:57] + "..."
        parts.append(f"{key}={s}")
    return f"{tool_name}(" + ", ".join(parts) + ")"


def _result_block(tool_use_id: str, content: str, is_error: bool) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content or "(no output)",
        "is_error": is_error,
    }


def run_agent(
    goal: str,
    ctx: Context,
    llm: AgentLLM,
    gate: PermissionGate,
    sink: EventSink,
    messages: list | None = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> list:
    """Run the loop until the model stops, is cancelled, or hits the step cap.

    Returns the message list so a session can continue the conversation.
    """
    registry = build_registry()
    tool_schemas = [t.api_schema() for t in registry.values()]
    system = build_system_prompt(ctx)

    messages = messages or []
    # A clean turn always ends on an assistant message. A trailing user message
    # means the previous run was cut off mid-step — start a fresh thread.
    if messages and messages[-1].get("role") == "user":
        messages = []
    messages.append({"role": "user", "content": goal})

    max_steps = ctx.cfg.get("agent", {}).get("max_steps", 60)
    hit_cap = True
    for _step in range(max_steps):
        if cancel_check is not None and cancel_check():
            sink.emit("notice", text="Cancelled by user.")
            hit_cap = False
            break

        response = llm.turn(system, tool_schemas, messages)
        messages.append({"role": "assistant", "content": response.content})

        for block in response.content:
            if block.type == "text" and block.text.strip():
                sink.emit("assistant_text", text=block.text.strip())

        if response.stop_reason == "max_tokens":
            sink.emit("notice", text="Model hit max_tokens — response may be incomplete.")

        if response.stop_reason != "tool_use":
            hit_cap = False
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            tool_input = block.input or {}
            label = _call_label(block.name, tool_input)
            sink.emit("tool_call", id=block.id, name=block.name,
                      input=tool_input, label=label)

            tool = registry.get(block.name)
            if tool is None:
                msg = f"Unknown tool '{block.name}'."
                sink.emit("tool_result", id=block.id, name=block.name, ok=False, preview=msg)
                tool_results.append(_result_block(block.id, msg, True))
                continue

            allowed, reason = gate.check(tool, label)
            if not allowed:
                sink.emit("tool_result", id=block.id, name=block.name, ok=False, preview=reason)
                tool_results.append(_result_block(block.id, reason, True))
                continue

            try:
                result = tool.handler(tool_input, ctx)
            except Exception as exc:   # a tool bug must not kill the whole loop
                result = ToolResult(f"Tool raised an exception: {exc!r}", is_error=True)

            sink.emit("tool_result", id=block.id, name=block.name,
                      ok=not result.is_error,
                      preview=(result.content or "")[:_PREVIEW_CHAR_CAP])
            tool_results.append(_result_block(block.id, _truncate(result.content), result.is_error))

        messages.append({"role": "user", "content": tool_results})

    if hit_cap:
        sink.emit("notice", text=f"Reached the {max_steps}-step limit — stopping.")
    sink.emit("turn_done", usage=llm.usage_summary())
    return messages
