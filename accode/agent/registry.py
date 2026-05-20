"""Tool registry — assembles every tool the agent can call."""
from __future__ import annotations

from accode.agent.tooling import Tool
from accode.tools.generic import GENERIC_TOOLS
from accode.tools.migration import MIGRATION_TOOLS

# Generic coding tools first, migration toolset second.
ALL_TOOLS: list[Tool] = [*GENERIC_TOOLS, *MIGRATION_TOOLS]


def build_registry() -> dict[str, Tool]:
    """name -> Tool, for O(1) dispatch in the loop."""
    return {t.name: t for t in ALL_TOOLS}
