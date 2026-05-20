"""Permission gate — decides whether a tool call may run.

The gate owns *policy* (allow / ask / deny, plus session memory). The act of
*asking a human* is delegated to a prompter callable, so the same gate works
for the CLI (console prompt) and the web frontend (prompt over the wire).
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from accode.agent.tooling import PERMISSION_ALLOW, PERMISSION_DENY, Tool

# A prompter is called for `ask` tools. It returns: 'once' | 'always' | 'deny'.
Prompter = Callable[[Tool, str], str]


class PermissionGate:
    def __init__(
        self,
        cfg: dict[str, Any],
        auto_approve: bool = False,
        prompter: Optional[Prompter] = None,
    ):
        # per-tool override from config: agent.permissions.<tool> = allow|ask|deny
        self._overrides: dict[str, str] = dict(cfg.get("agent", {}).get("permissions", {}))
        self._auto_approve = auto_approve
        self._prompter = prompter
        self._session_allow: set[str] = set()   # tools the user said "always" for

    def _policy(self, tool: Tool) -> str:
        return self._overrides.get(tool.name, tool.permission)

    def check(self, tool: Tool, label: str) -> tuple[bool, str]:
        """Return (allowed, reason_if_denied)."""
        policy = self._policy(tool)
        if policy == PERMISSION_DENY:
            return False, f"Tool '{tool.name}' is denied by configuration."
        if policy == PERMISSION_ALLOW:
            return True, ""
        # policy == ask
        if self._auto_approve or tool.name in self._session_allow:
            return True, ""
        if self._prompter is None:
            return False, f"Tool '{tool.name}' needs approval but no prompter is available."
        decision = self._prompter(tool, label)
        if decision == "always":
            self._session_allow.add(tool.name)
            return True, ""
        if decision == "once":
            return True, ""
        return False, "User denied this tool call."
