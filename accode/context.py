"""The shared Context object handed to every tool handler."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console


@dataclass
class Context:
    """Everything a tool needs that is not in its own input arguments."""

    cfg: dict[str, Any]
    cwd: Path
    console: Console

    def resolve(self, path: str) -> Path:
        """Resolve a possibly-relative path against the agent's working directory."""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.cwd / p
        return p.resolve()
