"""Configuration loader for Accode.

One config file feeds two consumers:
  * the agentic loop      → reads `anthropic` + `agent`
  * the migration engine  → reads `gcp`, `dataset_map`, `path_map`, `output`, ...

Everything has a default so Accode can run with nothing but an
ANTHROPIC_API_KEY in the environment.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_NAMES = ("config.yaml", "config.yml")


def find_config(explicit: str | None = None) -> Path | None:
    """Locate the config file: an explicit path, else config.yaml in the cwd."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    for name in _DEFAULT_CONFIG_NAMES:
        p = Path.cwd() / name
        if p.exists():
            return p
    return None


def load_config(config_path: str | None = None) -> dict[str, Any]:
    """Load and default-fill the Accode config dictionary."""
    cfg: dict[str, Any] = {}
    path = find_config(config_path)
    if path is not None:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    elif config_path:
        raise FileNotFoundError(f"Config file not found: {config_path}")

    # --- anthropic: shared by the agent loop AND the migration converters ---
    anthropic = cfg.setdefault("anthropic", {})
    if not anthropic.get("api_key"):
        anthropic["api_key"] = os.environ.get("ANTHROPIC_API_KEY")
    anthropic.setdefault("model", "claude-sonnet-4-6")
    anthropic.setdefault("max_tokens", 16000)

    # --- agent loop settings ---
    agent = cfg.setdefault("agent", {})
    agent.setdefault("model", anthropic["model"])   # loop model; may differ from converter model
    agent.setdefault("max_tokens", 8192)
    agent.setdefault("max_steps", 60)
    agent.setdefault("permissions", {})             # per-tool override: allow | ask | deny

    # --- migration engine settings (consumed by accode/engine/*) ---
    cfg.setdefault("gcp", {})
    cfg.setdefault("dataset_map", {})
    cfg.setdefault("path_map", {})
    cfg.setdefault("gcs_bucket", "gs://my-bucket")
    cfg.setdefault("query_parameter_types", {})
    out = cfg.setdefault("output", {})
    out.setdefault("dir", "./migrated")
    out.setdefault("overwrite", True)
    cfg.setdefault("dag_xml_dirs", ["event_engine", "scheduler", "schedulers", "workflows", "dag"])
    cfg.setdefault("test_dirs", ["tests", "test"])

    return cfg
