"""Static syntax validation for converted shell and Python files.

Runs lightweight syntax checks on every non-SQL output:
  * `.sh` files  → `bash -n script.sh` (no execution, just parse)
  * `.py` files  → Python's built-in `compile()` (no execution, no .pyc)

These are zero-cost, zero-network checks that catch the most common LLM
output artifacts: stray markdown fences, unterminated quotes, bad
indentation, mismatched brackets, etc.

SQL files are skipped — they already get BigQuery dry-run validation.
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyntaxFailure:
    rel_path: str          # path relative to output_dir
    file_type: str         # "shell" or "python"
    error: str             # short error message (first line, truncated)


@dataclass
class SyntaxCheckResult:
    ran: bool
    sh_checked: int = 0
    sh_failed: int = 0
    py_checked: int = 0
    py_failed: int = 0
    failures: list[SyntaxFailure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)   # e.g. "bash not on PATH"


class SyntaxChecker:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        # Resolve bash once — most failures are "bash not installed on Windows"
        self._bash_path = shutil.which("bash")

    def check_all(self) -> SyntaxCheckResult:
        result = SyntaxCheckResult(ran=True)

        if not self.output_dir.exists():
            result.ran = False
            result.notes.append(f"Output dir does not exist: {self.output_dir}")
            return result

        # --- Shell ---
        sh_files = sorted(self.output_dir.rglob("*.sh"))
        if sh_files and not self._bash_path:
            result.notes.append(
                f"bash not on PATH — {len(sh_files)} .sh file(s) not checked "
                "(install Git Bash on Windows, or run on Linux/macOS)"
            )
        for sh_path in sh_files:
            if not self._bash_path:
                continue   # do not inflate sh_checked when we cannot actually check
            result.sh_checked += 1
            err = self._check_shell(sh_path)
            if err:
                result.sh_failed += 1
                result.failures.append(SyntaxFailure(
                    rel_path=self._rel(sh_path),
                    file_type="shell",
                    error=err,
                ))

        # --- Python ---
        for py_path in sorted(self.output_dir.rglob("*.py")):
            result.py_checked += 1
            err = self._check_python(py_path)
            if err:
                result.py_failed += 1
                result.failures.append(SyntaxFailure(
                    rel_path=self._rel(py_path),
                    file_type="python",
                    error=err,
                ))

        return result

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.output_dir))
        except ValueError:
            return str(path)

    def _check_shell(self, path: Path) -> str | None:
        """Run `bash -n` — parse only, no execution."""
        try:
            proc = subprocess.run(
                [self._bash_path, "-n", str(path)],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode == 0:
                return None
            msg = (proc.stderr or proc.stdout or "").strip()
            # bash error format: "script.sh: line N: <error>" — keep the meat
            first = msg.splitlines()[0] if msg else f"bash -n exit {proc.returncode}"
            return first[:500]
        except subprocess.TimeoutExpired:
            return "bash -n timed out after 15s"
        except FileNotFoundError:
            # bash disappeared between init and now — treat as skip
            return None
        except Exception as e:
            return f"Could not run bash -n: {e}"

    def _check_python(self, path: Path) -> str | None:
        """Use built-in compile() — syntax-only, no .pyc files written."""
        try:
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")
            return None
        except SyntaxError as e:
            line = f" (line {e.lineno})" if e.lineno else ""
            return f"SyntaxError: {e.msg}{line}"
        except (UnicodeDecodeError, OSError) as e:
            return f"Could not read file: {e}"
        except Exception as e:
            return f"compile() failed: {e.__class__.__name__}: {e}"
