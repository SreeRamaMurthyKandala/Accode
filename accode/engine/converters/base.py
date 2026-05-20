"""Base class shared by all file-type converters."""
import re as _re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ConversionResult:
    content: str
    notes: list[str] = field(default_factory=list)
    needs_review: bool = False


class BaseConverter(ABC):
    def __init__(self, cfg: dict[str, Any], llm):
        self.cfg = cfg
        self.llm = llm
        self.dataset_map: dict[str, str] = cfg.get("dataset_map", {})
        self.path_map: dict[str, str] = cfg.get("path_map", {})
        self.gcp_project: str = cfg.get("gcp", {}).get("project", "my-project")
        self.gcs_bucket: str = cfg.get("gcs_bucket", "gs://my-bucket")

    @abstractmethod
    def convert(self, source: Path) -> ConversionResult: ...

    def map_dataset(self, hive_db: str) -> str:
        return self.dataset_map.get(hive_db, hive_db)

    def map_path(self, hdfs_path: str) -> str:
        for prefix, gcs_prefix in sorted(self.path_map.items(), key=lambda x: -len(x[0])):
            if hdfs_path.startswith(prefix):
                return gcs_prefix + hdfs_path[len(prefix):]
        return self.gcs_bucket + hdfs_path

    def qualified_table(self, hive_db: str, table: str) -> str:
        dataset = self.map_dataset(hive_db)
        return f"`{self.gcp_project}.{dataset}.{table}`"

    def _hdfs_path_pattern(self, capture: bool = False) -> str:
        """Build a regex matching any HDFS path prefix from path_map keys.

        Returns an empty string if path_map is empty (caller should skip substitution).
        `capture=True` wraps the prefix in a capture group for use in quoted-string patterns.
        """
        if not self.path_map:
            return ""
        # Escape each prefix and sort longest-first for correct greedy matching
        prefixes = sorted(
            (_re.escape(p) for p in self.path_map),
            key=len,
            reverse=True,
        )
        alternation = "|".join(prefixes)
        if capture:
            return f"({alternation})" + r"\S*"
        return f"(?:{alternation})" + r"\S+"
