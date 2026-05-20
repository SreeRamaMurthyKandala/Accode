"""Converter map and source/output path helpers shared by the migration tools.

This is the small amount of glue the original orchestrator kept hard-coded
(`_CONVERTER_MAP`, output-path computation, `_lookup_source`). It lives here
so each migration tool can stay independent.
"""
from __future__ import annotations

from pathlib import Path

from accode.engine.converters import (
    ConfigConverter,
    DAGConverter,
    FiltersConfigConverter,
    HDLConverter,
    HQLConverter,
    PySparkConverter,
    ShellConverter,
    TestFileConverter,
)
from accode.engine.scanner import FileType

# FileType -> (converter class, output extension, place under dags/ subdir)
CONVERTER_MAP: dict[FileType, tuple[type, str, bool]] = {
    FileType.HQL:            (HQLConverter,           ".sql",    False),
    FileType.HDL:            (HDLConverter,           ".sql",    False),
    FileType.SHELL:          (ShellConverter,         ".sh",     False),
    FileType.PYSPARK:        (PySparkConverter,       ".py",     False),
    FileType.CONFIG:         (ConfigConverter,        ".config", False),
    FileType.FILTERS_CONFIG: (FiltersConfigConverter, ".config", False),
    FileType.DAG_XML:        (DAGConverter,           ".py",     True),
    FileType.TEST_FILE:      (TestFileConverter,      ".py",     False),
}

# File types whose .sql output is worth a BigQuery dry-run.
VALIDATABLE = {FileType.HQL, FileType.HDL}


def output_path_for(rel_path: Path, file_type: FileType, output_dir: Path) -> Path:
    """Compute where a converted file lands, mirroring the source repo tree."""
    _, ext, in_dags = CONVERTER_MAP[file_type]
    if in_dags:
        return (Path(output_dir) / "dags" / rel_path.stem).with_suffix(ext)
    return Path(output_dir) / rel_path.with_suffix(ext)


def lookup_source(converted_path: Path, output_dir: Path, repo_root: Path) -> Path | None:
    """Reverse-map a converted output file back to its original Hive source.

    Used by migration_fix so the repair LLM sees the original intent, not just
    its own (possibly wrong) translation. Returns None if no source is found.
    """
    output_dir = Path(output_dir)
    repo_root = Path(repo_root)
    try:
        rel = Path(converted_path).relative_to(output_dir)
    except ValueError:
        return None

    candidates: list[Path] = []
    if rel.suffix == ".sql":
        candidates.append(repo_root / rel.with_suffix(".hql"))
        candidates.append(repo_root / rel.with_suffix(".hdl"))
    elif rel.suffix == ".py":
        candidates.append(repo_root / rel)                       # PySpark / test
        if rel.parts and rel.parts[0] == "dags":                 # DAG XML -> dags/<name>.py
            xml_name = Path(rel.name).with_suffix(".xml").name
            candidates.extend(repo_root.rglob(xml_name))
    else:
        candidates.append(repo_root / rel)

    for c in candidates:
        if c.exists():
            return c
    return None
