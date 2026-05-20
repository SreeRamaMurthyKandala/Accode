"""Second-pass fix agent — re-converts failing files using error context.

After conversion + validation + test runs produce a list of failing files,
this agent sends the failing file's current content together with the
specific error back to Claude and asks for a corrected version.

Three error sources are supported:
  * BigQuery dry-run failures (from VALIDATION_ERRORS.log / orchestrator)
  * pytest failures (from running migrated/tests/)
  * Files flagged "Needs Human Review" with `# TODO:` markers
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .llm import LLMClient


FIX_SYSTEM_PROMPT = """\
You are a senior data engineer doing a second-pass repair on a file that was
auto-converted from Hive/Hadoop to GCP. The first pass produced output that
failed validation. You will receive:

  1. The original Hive source file (for reference)
  2. The current (broken) converted file
  3. The specific error or TODO marker that needs fixing
  4. The error type (BQ_DRY_RUN, PYTEST, SYNTAX, or TODO_REVIEW)

Produce the corrected version of the converted file.

## Rules

- Return ONLY the fixed file contents — no markdown fences, no explanation, no preamble
- Preserve the `-- Converted from:` / `# Converted from:` header line
- Preserve all comments from the original that are still accurate
- Only change what is necessary to fix the reported error
- If the error is ambiguous, choose the most conservative fix and add a
  `-- TODO:` comment explaining what you assumed
- Do NOT introduce new TODOs unless absolutely necessary
- If the fix requires schema knowledge you don't have (e.g. you don't know
  the type of a column), make a reasonable Hive-convention assumption (STRING
  for `*_dt`, `*_timestamp`, `*_id` if unsure) and note it
- For BigQuery: use Standard SQL, backtick-quoted `project.dataset.table` refs,
  `@param` for runtime parameters, CAST when types may differ

## Specific guidance per error type

### BQ_DRY_RUN errors
- `Type mismatch` / `cannot be inserted into column X which has type Y` →
  wrap the source expression with `CAST(expr AS <target_type>)`
- `Unrecognized name: X` → check for a typo or unqualified table reference
- `Unexpected "@"` → the @param syntax should be valid BQ standard SQL; if
  it appears in an unsupported position, replace with a literal or rewrite
- `PARTITION BY expression must be ...` → remove the PARTITION BY clause and
  add a TODO; the user will add partitioning manually

### PYTEST errors
- `ImportError` / `ModuleNotFoundError` → fix the import path; the test
  package is `migrated/tests/`, fixtures live in `conftest.py` in the same dir
- `AssertionError` → the test's expected vs. actual no longer matches; verify
  the filter logic in the converted file matches the original HQL filter, NOT
  the test's stale PySpark expectation
- `SyntaxError` → typically a stray markdown fence (```python ... ```) left in
  by the LLM. Strip it.

### SYNTAX errors (from `bash -n` or Python `compile()`)
- These are pre-flight static checks on .sh and .py outputs, run before any
  test or BQ validation. The file is structurally broken — it would fail
  before it even runs.
- For Python (`SyntaxError`):
  * Most common cause: stray markdown fence (```python at top, ``` at bottom)
    left in by the previous LLM pass. Strip it.
  * Unterminated triple-quoted string → close it
  * Mixed tabs/spaces → normalize to 4-space indent
  * Backtick-quoted code blocks anywhere in the file → remove them
- For shell (`bash -n` errors like "unexpected EOF", "unexpected token"):
  * Unclosed quotes, heredocs, `if/fi`, `for/done`, `case/esac` blocks
  * Stray markdown fence at file top (```bash ... ```) — strip it
  * Windows line endings (CRLF) in heredocs can break bash — keep LF only
- Be surgical: fix ONLY the structural problem. Do not rewrite logic.

### TODO_REVIEW
- Look for `-- TODO:` / `# TODO:` in the file
- Resolve each TODO if you have enough information; otherwise leave it but
  refine the TODO to be more specific
"""


@dataclass
class FixResult:
    rel_path: str
    fixed: bool
    notes: list[str]


class FixAgent:
    def __init__(self, cfg: dict[str, Any], llm: LLMClient):
        self.cfg = cfg
        self.llm = llm

    def fix_file(
        self,
        source_original: Path,
        converted_path: Path,
        error_message: str,
        error_type: str,
    ) -> FixResult:
        """Send a single failing file back to Claude with the error and write the fix."""
        notes: list[str] = []
        try:
            original_text = source_original.read_text(encoding="utf-8", errors="replace") \
                if source_original.exists() else "(original source not available)"
            converted_text = converted_path.read_text(encoding="utf-8")
        except Exception as e:
            return FixResult(
                rel_path=str(converted_path),
                fixed=False,
                notes=[f"Could not read file: {e}"],
            )

        user_message = (
            f"ERROR_TYPE: {error_type}\n"
            f"FILE: {converted_path.name}\n\n"
            f"=== ORIGINAL HIVE SOURCE ({source_original.name if source_original.exists() else 'n/a'}) ===\n"
            f"{original_text}\n\n"
            f"=== CURRENT BROKEN CONVERTED FILE ({converted_path.name}) ===\n"
            f"{converted_text}\n\n"
            f"=== ERROR MESSAGE ===\n"
            f"{error_message}\n"
        )
        fixed_text = self.llm.convert(FIX_SYSTEM_PROMPT, user_message)
        if not fixed_text.strip():
            return FixResult(str(converted_path), fixed=False, notes=["Empty response from LLM"])

        converted_path.write_text(fixed_text, encoding="utf-8")
        notes.append(f"Fixed (error_type={error_type})")
        return FixResult(str(converted_path), fixed=True, notes=notes)
