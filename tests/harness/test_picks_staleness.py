"""Tests distinguishing signal_date staleness vs file_mtime staleness."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dipdiver.harness.picks import (
    signal_file_mtime_hours,
    signal_freshness_hours,
)


def _write(path: Path, latest_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"date,symbol,score\n{latest_date},AAPL,0.1\n",
        encoding="utf-8",
    )


def test_signal_freshness_reads_csv_latest_date(tmp_path: Path):
    """signal_freshness_hours is gated by the date column, not file mtime."""
    p = tmp_path / "sig.csv"
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    _write(p, yesterday)
    age = signal_freshness_hours(p)
    assert age is not None
    # "yesterday" date parsed as midnight UTC; depending on current UTC time
    # the age is anywhere in [0, 48) hours. The key invariant is it's at most 2 days.
    assert 0 <= age < 48


def test_signal_freshness_stays_stale_after_file_rewrite_with_old_date(tmp_path: Path):
    """The bug the user hit: signal_refresh rewrites the CSV but the date
    inside it is still the M1 model's test_end. Freshness must reflect that.
    """
    p = tmp_path / "sig.csv"
    _write(p, "2025-12-31")
    # Touch the file to mimic a fresh signal_refresh.
    p.touch()
    age = signal_freshness_hours(p)
    assert age is not None
    # Test runs in 2026+; the signal date is still in the distant past.
    assert age > 24 * 30  # > 30 days, definitely stale


def test_signal_file_mtime_picks_up_a_fresh_rewrite(tmp_path: Path):
    """The new helper — distinct from signal_freshness_hours — DOES update."""
    p = tmp_path / "sig.csv"
    _write(p, "2025-12-31")  # old signal date
    # Force the mtime to right now
    now = time.time()
    os.utime(p, (now, now))
    mtime_age = signal_file_mtime_hours(p)
    assert mtime_age is not None
    assert mtime_age < 0.05  # less than 3 minutes — basically just-written


def test_signal_file_mtime_returns_none_when_missing(tmp_path: Path):
    p = tmp_path / "missing.csv"
    assert signal_file_mtime_hours(p) is None


def test_signal_freshness_returns_none_when_missing(tmp_path: Path):
    p = tmp_path / "missing.csv"
    assert signal_freshness_hours(p) is None
