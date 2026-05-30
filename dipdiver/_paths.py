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
