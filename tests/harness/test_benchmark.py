"""Tests for the M14 benchmark P&L helper."""

from __future__ import annotations

from datetime import date as date_cls
from pathlib import Path

import pytest

from dipdiver.harness import benchmark as bench


def _write_series(tmp_path: Path, symbol: str, rows: list[tuple[str, float]]):
    p = tmp_path / "data" / "benchmarks" / f"{symbol}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,close"]
    for d, c in rows:
        lines.append(f"{d},{c}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_load_series_empty_when_file_missing(tmp_path: Path, monkeypatch):
    bench.reset_cache()
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    assert bench.load_series("SPY") == {}


def test_load_series_reads_csv(tmp_path: Path, monkeypatch):
    bench.reset_cache()
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    _write_series(tmp_path, "SPY", [
        ("2026-06-03", 540.0),
        ("2026-06-04", 545.0),
    ])
    series = bench.load_series("SPY")
    assert series["2026-06-03"] == 540.0
    assert series["2026-06-04"] == 545.0


def test_close_on_or_before_walks_back():
    series = {"2026-06-05": 100.0}
    out = bench.close_on_or_before(series, date_cls(2026, 6, 7))
    assert out == 100.0  # Sunday → Friday


def test_close_on_or_before_returns_none_after_walkback():
    series = {"2026-05-01": 100.0}
    out = bench.close_on_or_before(series, date_cls(2026, 6, 7))
    assert out is None


def test_daily_excess_pct(tmp_path: Path, monkeypatch):
    bench.reset_cache()
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    _write_series(tmp_path, "SPY", [
        ("2026-06-03", 540.0),
        ("2026-06-04", 545.4),  # +1%
    ])
    pct = bench.daily_excess_pct(symbol="SPY", target_date=date_cls(2026, 6, 4))
    assert pct is not None
    assert pct == pytest.approx(0.01, abs=1e-6)


def test_daily_excess_pct_none_when_no_data(tmp_path: Path, monkeypatch):
    bench.reset_cache()
    monkeypatch.setattr(bench, "repo_root", lambda: tmp_path)
    pct = bench.daily_excess_pct(symbol="SPY", target_date=date_cls(2026, 6, 4))
    assert pct is None
