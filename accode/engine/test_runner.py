"""Runs the converted pytest suite and reports per-file failures."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TestFailure:
    rel_path: str          # path of the test file that contained the failure
    error_excerpt: str     # short error message (first ~30 lines)


@dataclass
class TestRunResult:
    ran: bool                          # did pytest execute at all?
    return_code: int
    passed: int
    failed: int
    errors: int
    failures: list[TestFailure]
    raw_output: str
    skipped_reason: str = ""           # if ran=False, why


class TestRunner:
    def __init__(self, output_dir: Path):
        self.output_dir = output_dir
        self.tests_dir = output_dir / "tests"

    def run(self) -> TestRunResult:
        if not self.tests_dir.exists():
            return TestRunResult(
                ran=False, return_code=0, passed=0, failed=0, errors=0,
                failures=[], raw_output="",
                skipped_reason="No tests/ directory in output",
            )

        # Capture full output; --tb=short keeps the traceback compact
        cmd = [sys.executable, "-m", "pytest", str(self.tests_dir),
               "--tb=short", "-q", "--no-header"]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=300, cwd=str(self.output_dir),
            )
        except FileNotFoundError:
            return TestRunResult(
                ran=False, return_code=-1, passed=0, failed=0, errors=0,
                failures=[], raw_output="",
                skipped_reason="pytest not installed (pip install pytest)",
            )
        except subprocess.TimeoutExpired:
            return TestRunResult(
                ran=False, return_code=-1, passed=0, failed=0, errors=0,
                failures=[], raw_output="",
                skipped_reason="pytest timed out after 5 minutes",
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        passed, failed, errors = self._parse_summary(output)
        failures = self._parse_failures(output)

        return TestRunResult(
            ran=True,
            return_code=proc.returncode,
            passed=passed,
            failed=failed,
            errors=errors,
            failures=failures,
            raw_output=output,
        )

    @staticmethod
    def _parse_summary(output: str) -> tuple[int, int, int]:
        """Extract pass/fail/error counts from pytest's summary line."""
        import re
        passed = failed = errors = 0
        # Examples: "1 failed, 4 passed in 0.12s", "5 passed in 0.10s"
        for m in re.finditer(r"(\d+)\s+(passed|failed|error[s]?)", output):
            n = int(m.group(1))
            kind = m.group(2)
            if kind == "passed":
                passed = n
            elif kind == "failed":
                failed = n
            elif kind.startswith("error"):
                errors = n
        return passed, failed, errors

    def _parse_failures(self, output: str) -> list[TestFailure]:
        """Pull out per-file error excerpts from pytest output.

        Handles three shapes pytest produces:
          1. ``FAILED tests/foo.py::TestBar::test_baz - AssertionError: ...``
          2. ``ERROR tests/foo.py::test_baz - ImportError: ...`` (test-time error
             with the standard `` - <msg>`` suffix)
          3. ``ERROR tests/foo.py`` (collection error — no ``- <msg>`` suffix;
             the real error lives in a separate ``ERROR collecting`` block
             above, which we look up by file path).
        """
        import re
        failures: list[TestFailure] = []
        seen: set[str] = set()
        lines = output.splitlines()

        # Pre-index "ERROR collecting <file>" blocks so we can attach a real
        # message to bare-form collection errors (case 3 above). pytest prints:
        #   ___________ ERROR collecting tests/test_foo.py ___________
        #   tests/test_foo.py:5: in <module>
        #       from foo import bar
        #   E   ModuleNotFoundError: No module named 'foo'
        collect_errors: dict[str, str] = {}
        i = 0
        block_header = re.compile(r"_+\s*ERROR collecting (\S+\.py)\s*_+")
        while i < len(lines):
            m = block_header.search(lines[i])
            if m:
                test_file = m.group(1)
                # Walk forward, grabbing the most informative line ("E   ...")
                # or, failing that, the first non-empty body line.
                excerpt_lines: list[str] = []
                j = i + 1
                while j < len(lines) and not lines[j].startswith("===") \
                        and not block_header.search(lines[j]):
                    excerpt_lines.append(lines[j])
                    j += 1
                # Prefer "E   " lines (pytest's error marker); else first 5 lines
                e_lines = [ln for ln in excerpt_lines if ln.startswith("E   ")]
                if e_lines:
                    excerpt = "\n".join(e_lines)[:500]
                else:
                    excerpt = "\n".join(
                        ln for ln in excerpt_lines[:5] if ln.strip()
                    )[:500]
                collect_errors[test_file] = excerpt or "(collection error)"
                i = j
                continue
            i += 1

        # Now walk the short summary section. Both with-suffix and bare lines
        # are accepted; the suffix is optional.
        summary_re = re.compile(
            r"^(?:FAILED|ERROR)\s+(\S+\.py)(?:::\S+)?(?:\s*-\s*(.+))?$"
        )
        for line in lines:
            m = summary_re.match(line)
            if not m:
                continue
            test_file = m.group(1)
            inline_msg = (m.group(2) or "").strip()
            if test_file in seen:
                continue
            seen.add(test_file)

            # Prefer the inline message; fall back to the collection-error block
            if inline_msg:
                err = inline_msg[:500]
            elif test_file in collect_errors:
                err = collect_errors[test_file]
            else:
                err = "(no error message captured)"
            failures.append(TestFailure(rel_path=test_file, error_excerpt=err))

        # Also include any ERROR-collecting blocks whose file never made it into
        # the short summary (rare, but possible with --co-only style runs).
        for test_file, excerpt in collect_errors.items():
            if test_file in seen:
                continue
            seen.add(test_file)
            failures.append(TestFailure(rel_path=test_file, error_excerpt=excerpt))

        # Last-resort fallback: tests failed but we couldn't identify any file
        if not failures and ("failed" in output.lower() or "error" in output.lower()):
            tail = "\n".join(lines[-40:])
            failures.append(TestFailure(rel_path="(unknown)", error_excerpt=tail))
        return failures
