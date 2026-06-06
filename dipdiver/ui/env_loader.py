"""Auto-load .env.m2 (and .env.m2.example as a local-dev fallback) at UI boot.

Why this exists
---------------
Standalone scripts (m3_live_alpaca, m2_lite_run, m6_nightly) explicitly call
`_load_env_file(repo_root() / ".env.m2")` before they touch any credentials.
The UI never had that step — every page that constructs an `AlpacaPaperClient`
or DeepSeek client read straight from `os.environ` and surfaced the missing
key as an "Alpaca unreachable" error card.

This module fixes that by:
  1. Reading `.env.m2` at app startup and injecting its values into os.environ.
  2. Falling back to `.env.m2.example` so local dev that keeps real keys in the
     example file (because `.env.m2` is gitignored and the developer hasn't
     populated it) still picks them up.
  3. Skipping placeholder values like `PK_REPLACE_ME` so we never load junk
     and incorrectly trigger downstream "credentials valid" code paths.
  4. NEVER overwriting an existing env var. Production VMs that set the keys
     via system env or systemd units stay in control.

The candidate files are checked in priority order — `.env.m2` first (real
config) then `.env.m2.example` (the developer-tracked template).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from dipdiver._paths import repo_root


log = logging.getLogger(__name__)


# Values starting with any of these are treated as placeholders and skipped.
# Mirrors the strings in the committed `.env.m2.example`.
PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "REPLACE_ME",
    "PK_REPLACE_ME",
    "sk-REPLACE_ME",
    "your-",       # common "your-key-here" pattern
    "xxxx",
    "changeme",
)


@dataclass(frozen=True)
class EnvLoadReport:
    """What env_loader actually did, surfaced in logs + /health."""

    files_checked: tuple[str, ...]
    files_loaded: tuple[str, ...]
    vars_set: tuple[str, ...] = field(default_factory=tuple)
    vars_skipped_placeholder: tuple[str, ...] = field(default_factory=tuple)
    vars_skipped_already_set: tuple[str, ...] = field(default_factory=tuple)


# Module-level cache so /health and other routes can show what happened.
_last_report: EnvLoadReport | None = None


def last_report() -> EnvLoadReport | None:
    return _last_report


def _is_placeholder(value: str) -> bool:
    v = value.strip().strip("'").strip('"')
    if not v:
        return True
    for marker in PLACEHOLDER_MARKERS:
        if marker in v:
            return True
    return False


def _parse_env_file(path: Path) -> Iterable[tuple[str, str]]:
    """Yield (key, raw_value) pairs from a .env file."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if not key:
            continue
        yield key, val


def load_env_files(
    *,
    candidates: tuple[Path, ...] | None = None,
) -> EnvLoadReport:
    """Apply .env files to os.environ, in priority order.

    Honours these rules:
      - never overwrite a value already in os.environ
      - skip placeholder values
      - load each file only once; missing files are silently OK
      - record per-file outcomes for transparency on /health

    Pass `candidates` for tests; defaults to (.env.m2, .env.m2.example) under
    the repo root.
    """
    global _last_report
    if candidates is None:
        repo = repo_root()
        candidates = (repo / ".env.m2", repo / ".env.m2.example")

    vars_set: list[str] = []
    vars_placeholder: list[str] = []
    vars_already_set: list[str] = []
    files_loaded: list[str] = []
    files_checked: list[str] = [str(c) for c in candidates]

    for path in candidates:
        if not path.exists():
            continue
        files_loaded.append(str(path))
        loaded_any = False
        for key, val in _parse_env_file(path):
            if key in os.environ:
                # Real env wins. Track once per key (first file that hit it).
                if key not in vars_already_set:
                    vars_already_set.append(key)
                continue
            if _is_placeholder(val):
                vars_placeholder.append(key)
                continue
            os.environ[key] = val
            vars_set.append(key)
            loaded_any = True
        if loaded_any:
            log.info(
                "env_loader: loaded %d vars from %s", len(vars_set), path.name,
            )

    report = EnvLoadReport(
        files_checked=tuple(files_checked),
        files_loaded=tuple(files_loaded),
        vars_set=tuple(vars_set),
        vars_skipped_placeholder=tuple(vars_placeholder),
        vars_skipped_already_set=tuple(vars_already_set),
    )
    _last_report = report
    return report


def reset_for_test() -> None:
    """Test-only: forget the cached report so a fresh call starts clean."""
    global _last_report
    _last_report = None
