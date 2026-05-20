"""migration_convert — convert Hive files to their GCP equivalents.

Wraps the engine's per-type converters — the orchestrator's `_process_file()`
loop. Writes converted files under the output directory and records run state
in `.accode_state.json` for the later stages to read.
"""
from __future__ import annotations

import shutil

from accode.agent.tooling import PERMISSION_ASK, Tool, ToolResult
from accode.context import Context
from accode.engine.llm import LLMClient
from accode.engine.scanner import FileType, RepoFile, classify_single, scan
from accode.paths import CONVERTER_MAP, output_path_for
from accode.state import load_state, save_state

_SUMMARY_LINE_LIMIT = 60


def _handler(inp: dict, ctx: Context) -> ToolResult:
    cfg = ctx.cfg
    repo_path = ctx.resolve(inp["repo_path"])
    if not repo_path.exists() or not repo_path.is_dir():
        return ToolResult(f"Repo path not found or not a directory: {repo_path}", is_error=True)
    output_dir = ctx.resolve(inp.get("output_dir") or cfg["output"]["dir"])
    clean = bool(inp.get("clean", False))
    only = inp.get("files")

    # --- build the work list -------------------------------------------------
    work: list[RepoFile] = []
    if only:
        for rel_str in only:
            target = (repo_path / rel_str).resolve()
            if not target.is_file():
                return ToolResult(f"File not found in repo: {rel_str}", is_error=True)
            try:
                rel = target.relative_to(repo_path)
            except ValueError:
                return ToolResult(f"File is outside the repo: {rel_str}", is_error=True)
            ftype = classify_single(target, rel, cfg.get("dag_xml_dirs"), cfg.get("test_dirs"))
            if ftype == FileType.SKIP:
                return ToolResult(f"Not a migration target: {rel_str}", is_error=True)
            work.append(RepoFile(path=target, rel_path=rel, file_type=ftype))
    else:
        scanned = scan(
            str(repo_path),
            dag_xml_dirs=cfg.get("dag_xml_dirs"),
            test_dirs=cfg.get("test_dirs"),
        )
        work = [f for f in scanned if f.file_type != FileType.SKIP]

    if not work:
        return ToolResult("No migration target files found in the repo.", is_error=True)

    # --- prepare the output directory ---------------------------------------
    if clean and not only and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        llm = LLMClient(cfg)
    except ValueError as exc:
        return ToolResult(f"Conversion LLM unavailable: {exc}", is_error=True)

    # --- convert each file ---------------------------------------------------
    converters: dict = {}
    results: list[dict] = []
    ok = review = errors = 0
    for repo_file in work:
        converter_cls, _, _ = CONVERTER_MAP[repo_file.file_type]
        converter = converters.get(repo_file.file_type)
        if converter is None:
            converter = converter_cls(cfg, llm)
            converters[repo_file.file_type] = converter

        out_path = output_path_for(repo_file.rel_path, repo_file.file_type, output_dir)
        entry = {
            "rel_path": str(repo_file.rel_path),
            "file_type": repo_file.file_type.value,
            "output_path": str(out_path),
            "status": "error",
            "notes": [],
            "validation_passed": None,
            "validation_error": None,
        }
        try:
            conversion = converter.convert(repo_file.path)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(conversion.content, encoding="utf-8")
            entry["status"] = "review" if conversion.needs_review else "converted"
            entry["notes"] = list(conversion.notes)
            if conversion.needs_review:
                review += 1
            else:
                ok += 1
        except Exception as exc:   # one bad file must not abort the whole convert
            entry["notes"] = [f"Conversion failed: {exc}"]
            errors += 1
        results.append(entry)

    # --- persist run state ---------------------------------------------------
    state = load_state(output_dir) or {}
    if only and state.get("files"):
        merged = {e["rel_path"]: e for e in state["files"]}
        for entry in results:
            merged[entry["rel_path"]] = entry
        state["files"] = list(merged.values())
    else:
        state["files"] = results
    state["repo_path"] = str(repo_path)
    state["output_dir"] = str(output_dir)
    save_state(output_dir, state)

    # --- summary -------------------------------------------------------------
    summary = [
        f"Converted {len(work)} file(s) -> {output_dir}",
        f"  ok={ok}  needs_review={review}  errors={errors}",
    ]
    flagged = [e for e in results if e["status"] in ("error", "review")]
    for entry in flagged[:_SUMMARY_LINE_LIMIT]:
        note = f" — {entry['notes'][0]}" if entry["notes"] else ""
        summary.append(f"  [{entry['status']}] {entry['rel_path']}{note}")
    if len(flagged) > _SUMMARY_LINE_LIMIT:
        summary.append(f"  ... [{len(flagged) - _SUMMARY_LINE_LIMIT} more flagged files]")
    summary.append("Run state recorded in .accode_state.json in the output directory.")
    summary.append("Next: migration_syntax_check, then (if GCP is configured) migration_bq_validate.")
    return ToolResult("\n".join(summary), is_error=(ok == 0 and review == 0))


TOOL = Tool(
    name="migration_convert",
    description=(
        "STAGE 2 of the Hive->GCP migration. Convert Hive files to their GCP "
        "equivalents (HQL/HDL -> BigQuery SQL, shell -> bq-CLI scripts, PySpark "
        "-> BigQuery Python, DAG-XML -> Airflow DAGs, tests -> pytest). Writes "
        "converted files under output_dir and records run state. By default "
        "converts every target file in the repo; pass `files` to convert only "
        "specific ones. Calls the conversion LLM once per file."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "repo_path": {"type": "string", "description": "Path to the Hive repository root."},
            "output_dir": {"type": "string", "description": "Where to write converted files (default: config output.dir)."},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional repo-relative file paths to convert. Omit to convert the whole repo.",
            },
            "clean": {"type": "boolean", "description": "Delete the output directory before a full convert (default false)."},
        },
        "required": ["repo_path"],
    },
    handler=_handler,
    permission=PERMISSION_ASK,
)
