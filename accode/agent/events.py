"""Event sink + permission prompter abstractions.

The agent loop is UI-agnostic: it emits structured events to a *sink* and asks
for tool permission through a *prompter*. The CLI uses a console sink/prompter;
the web frontend uses a queue sink and a blocking web prompter.

Event kinds emitted by the loop:
  assistant_text   {text}
  tool_call        {id, name, input, label}
  tool_result      {id, name, ok, preview}
  notice           {text}                  - max_tokens, step cap, cancellation
  error            {text}
  turn_done        {usage}                  - end of a run, with usage summary

Permission events are emitted by the web prompter, not the loop:
  permission_request   {id, tool, label}
  permission_resolved  {id, tool, decision}
"""
from __future__ import annotations

import queue
from typing import Any

from rich.console import Console


class EventSink:
    """Receives structured events from the agent loop."""

    def emit(self, kind: str, **fields: Any) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleSink(EventSink):
    """Renders loop events to a rich console — the CLI surface."""

    def __init__(self, console: Console):
        self._c = console

    def emit(self, kind: str, **f: Any) -> None:
        if kind == "assistant_text":
            self._c.print(f["text"])
        elif kind == "tool_call":
            self._c.print(f"[cyan]→[/] {f['label']}")
        elif kind == "tool_result":
            status = "[green]ok[/]" if f.get("ok") else "[red]error[/]"
            preview = (f.get("preview") or "").splitlines()
            first = preview[0][:120] if preview else ""
            self._c.print(f"  {status} [dim]{first}[/]")
        elif kind == "notice":
            self._c.print(f"[yellow]{f['text']}[/]")
        elif kind == "error":
            self._c.print(f"[red]Error:[/] {f['text']}")
        elif kind == "turn_done":
            self._c.print(f"\n[dim]{f['usage']}[/]")
        # permission_* / turn_end: nothing to render on the console


class QueueSink(EventSink):
    """Pushes events onto a thread-safe queue — the web frontend surface."""

    def __init__(self, q: "queue.Queue"):
        self._q = q

    def emit(self, kind: str, **fields: Any) -> None:
        self._q.put({"kind": kind, **fields})


class ConsolePrompter:
    """Asks for tool permission on the console.

    Returns one of: 'once', 'always', 'deny'.
    """

    def __init__(self, console: Console):
        self._c = console

    def __call__(self, tool, label: str) -> str:
        self._c.print(f"\n[yellow]Permission needed[/] — [bold]{label}[/]")
        self._c.print("  [dim][y] allow once   [a] allow this tool all session   [n] deny[/]")
        try:
            choice = input("  > ").strip().lower()
        except EOFError:
            choice = "n"
        if choice in ("a", "always"):
            return "always"
        if choice in ("y", "yes", ""):
            return "once"
        return "deny"
