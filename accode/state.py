"""Migration run-state — the shared 'bus' between the migration tools.

The original orchestrator threaded conversion results and DDL paths through
in-memory attributes. Accode's tools are independent processes-of-thought, so
the equivalent state is persisted to `<output_dir>/.accode_state.json`:

  * migration_convert      writes the file list + per-file conversion status
  * migration_bq_validate  updates per-file validation status + a `bq` summary
  * migration_run_tests    writes a `tests` summary
  * migration_report       reads all of it to render MIGRATION_REPORT.md

State shape::

    {
      "repo_path": str,
      "output_dir": str,
      "files": [
        {rel_path, file_type, output_path, status, notes[],
         validation_passed: bool|null, validation_error: str|null}
      ],
      "bq":    {"passed": int, "failures": [[rel, error], ...]},
      "tests": {"ran": bool, "passed": int, "failed": int,
                "errors": int, "failures": [[rel, excerpt], ...]}
    }
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_FILENAME = ".accode_state.json"


def state_path(output_dir: Path | str) -> Path:
    return Path(output_dir) / STATE_FILENAME


def load_state(output_dir: Path | str) -> dict[str, Any] | None:
    """Return the persisted state dict, or None if absent/corrupt."""
    p = state_path(output_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_state(output_dir: Path | str, state: dict[str, Any]) -> None:
    p = state_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")
