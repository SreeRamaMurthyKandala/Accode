"""Scan and classify all files in a Hive repository."""
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence


class FileType(str, Enum):
    HQL = "hql"                   # Hive SQL query → BigQuery SQL
    HDL = "hdl"                   # Hive DDL table definition → BigQuery DDL
    SHELL = "shell"               # Shell trigger script → GCP bash
    PYSPARK = "pyspark"           # PySpark job → BigQuery Python
    CONFIG = "config"             # Directory/paths config → GCP config
    FILTERS_CONFIG = "filters"    # Hive hivevar filter injection → BQ WHERE docs
    DAG_XML = "dag_xml"           # Scheduler XML → Airflow DAG
    TEST_FILE = "test"            # PySpark unit test → BigQuery pytest
    SKIP = "skip"                 # Not a migration target


@dataclass
class RepoFile:
    path: Path       # Absolute path
    rel_path: Path   # Relative to repo root
    file_type: FileType


# Default folder names that contain scheduler/event-engine XML files.
# Override via config: dag_xml_dirs: ["event_engine", "schedulers", ...]
_DEFAULT_DAG_XML_DIRS = ["event_engine", "scheduler", "schedulers", "workflows", "dag"]

# Default folder/path segments that contain test files.
_DEFAULT_TEST_DIRS = ["tests", "test"]


def scan(
    repo_path: str,
    dag_xml_dirs: Sequence[str] | None = None,
    test_dirs: Sequence[str] | None = None,
) -> list[RepoFile]:
    root = Path(repo_path).resolve()
    _dag_dirs = [d.lower() for d in (dag_xml_dirs or _DEFAULT_DAG_XML_DIRS)]
    _test_dirs = [d.lower() for d in (test_dirs or _DEFAULT_TEST_DIRS)]
    files: list[RepoFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts):
            continue
        ft = _classify(path, rel, _dag_dirs, _test_dirs)
        files.append(RepoFile(path=path, rel_path=rel, file_type=ft))
    return files


def classify_single(
    path: Path,
    rel: Path,
    dag_xml_dirs: Sequence[str] | None = None,
    test_dirs: Sequence[str] | None = None,
) -> FileType:
    """Public wrapper around the classifier for single-file workflows."""
    _dag_dirs = [d.lower() for d in (dag_xml_dirs or _DEFAULT_DAG_XML_DIRS)]
    _test_dirs = [d.lower() for d in (test_dirs or _DEFAULT_TEST_DIRS)]
    return _classify(path, rel, _dag_dirs, _test_dirs)


def _classify(
    path: Path,
    rel: Path,
    dag_xml_dirs: list[str],
    test_dirs: list[str],
) -> FileType:
    suffix = path.suffix.lower()
    rel_parts_lower = [p.lower() for p in rel.parts]

    if suffix == ".hql":
        return FileType.HQL

    if suffix == ".hdl":
        return FileType.HDL

    if suffix in (".sh", ".ksh"):
        return FileType.SHELL

    if suffix == ".config":
        content = path.read_text(errors="replace")
        if "hivevar:" in content:
            return FileType.FILTERS_CONFIG
        return FileType.CONFIG

    if suffix == ".xml" and any(d in rel_parts_lower for d in dag_xml_dirs):
        return FileType.DAG_XML

    if suffix == ".py":
        content = path.read_text(errors="replace")
        if any(d in rel_parts_lower for d in test_dirs):
            return FileType.TEST_FILE
        if "SparkSession" in content or "pyspark" in content.lower():
            return FileType.PYSPARK
        return FileType.SKIP

    return FileType.SKIP
