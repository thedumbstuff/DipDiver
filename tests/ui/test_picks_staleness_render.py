"""Verify /picks renders the RIGHT staleness banner: model out-of-window
vs file-refresh stale are mutually exclusive and direct the user differently.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _write_signals(repo_root: Path, stem: str, latest_date: str) -> Path:
    p = repo_root / "data" / "signals" / f"{stem}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"date,symbol,score\n{latest_date},AAPL,0.1\n{latest_date},AMZN,0.08\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def patched_repo(data_root: Path, monkeypatch):
    import dipdiver._paths as paths_mod
    import dipdiver.harness.picks as picks_mod
    monkeypatch.setattr(paths_mod, "repo_root", lambda: data_root)
    monkeypatch.setattr(picks_mod, "repo_root", lambda: data_root)
    return data_root


def test_old_signal_date_shows_model_out_of_window_banner(
    client: TestClient, patched_repo: Path,
):
    """User's reported bug: signal_refresh ran, file is fresh, but signal
    date is 2025-12-31 → must show the M1-retrain guidance, NOT the
    'trigger signal_refresh' message.
    """
    p = _write_signals(patched_repo, "dow30_lightgbm", "2025-12-31")
    # mtime is right now → file_refresh_stale is False
    os.utime(p, (time.time(), time.time()))

    r = client.get("/picks?universe=dow30")
    assert r.status_code == 200
    body = r.text
    assert "Model out-of-window" in body
    assert "m1_retrain" in body
    # The misleading "Signal file last regenerated" line MUST NOT appear.
    assert "Signal file last regenerated" not in body


def test_recent_signal_date_no_warning(client: TestClient, patched_repo: Path):
    """Yesterday's signal date + fresh mtime → no banner at all."""
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    p = _write_signals(patched_repo, "dow30_lightgbm", yesterday)
    os.utime(p, (time.time(), time.time()))

    r = client.get("/picks?universe=dow30")
    body = r.text
    assert "Model out-of-window" not in body
    assert "Signal file last regenerated" not in body


def test_recent_signal_date_but_stale_file_shows_file_warning(
    client: TestClient, patched_repo: Path,
):
    """Edge: signal date is recent (model still in window) but file mtime is
    very old → show the 'run signal_refresh' banner (not the m1_retrain one).
    """
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    p = _write_signals(patched_repo, "dow30_lightgbm", yesterday)
    # Set mtime to 3 days ago
    ts = time.time() - 3 * 24 * 3600
    os.utime(p, (ts, ts))

    r = client.get("/picks?universe=dow30")
    body = r.text
    assert "Signal file last regenerated" in body
    assert "Model out-of-window" not in body
