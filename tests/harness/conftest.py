"""Fixtures for harness (scoreboard + render) tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def tmp_scoreboard(tmp_path: Path) -> Path:
    """A fresh empty scoreboard.jsonl path under tmpdir."""
    return tmp_path / "scoreboard.jsonl"
