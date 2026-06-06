"""Repo-relative path helpers.

Avoids hard-coding absolute paths in YAML configs or scripts. The repo root is
located by walking up from this file until pyproject.toml is found.
"""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError("could not find repo root (no pyproject.toml above this file)")


def data_root() -> Path:
    """Default data root: <repo>/data/qlib, overridable via DIPDIVER_DATA_ROOT env."""
    override = os.environ.get("DIPDIVER_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root() / "data" / "qlib"


def ui_data_root() -> Path:
    """Root for UI-owned mutable state.

    On a dev machine this defaults to the repo (so scoreboard/, logs/, etc
    found there are used as-is). On a VM, set DIPDIVER_UI_DATA_ROOT to e.g.
    /var/lib/dipdiver and bind a persistent volume to that path.
    """
    override = os.environ.get("DIPDIVER_UI_DATA_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return repo_root()


def ui_scoreboard_path() -> Path:
    return ui_data_root() / "scoreboard" / "scoreboard.jsonl"


def ui_db_path() -> Path:
    return ui_data_root() / "db" / "ui.sqlite"


def ui_config_path() -> Path:
    """UI's mutable config (universe, providers, cost caps, etc). Distinct
    from the M1 baseline YAMLs which are code-owned."""
    return ui_data_root() / "config" / "ui_config.yaml"


def ui_schedules_path() -> Path:
    return ui_data_root() / "config" / "schedules.yaml"


def ui_logs_dir() -> Path:
    return ui_data_root() / "logs"


def ui_rendered_dir() -> Path:
    """Where scoreboard_render job writes SCOREBOARD.md (served by UI; never
    committed to repo per M8 decision 3)."""
    return ui_data_root() / "rendered"


def resolve_provider_uri(raw: str | Path) -> Path:
    """Resolve a Qlib provider_uri string.

    - Absolute paths stay absolute.
    - Paths starting with ~ are expanded.
    - Relative paths are joined to repo_root() so configs stay portable.
    """
    raw_str = str(raw)
    if raw_str.startswith("~"):
        return Path(raw_str).expanduser().resolve()
    p = Path(raw_str)
    if p.is_absolute():
        return p
    return (repo_root() / p).resolve()
