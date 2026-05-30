"""Tests for the data pipeline. Skip anything that requires network or pyqlib."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dipdiver.brain.baselines.data import (
    QLIB_FIELDS,
    VerifyReport,
    dump_to_qlib,
    verify_store,
)
from dipdiver.brain.baselines.universes import CRYPTO_BASKET


def _fake_frame(start: str = "2024-01-02", days: int = 5) -> "pd.DataFrame":  # type: ignore[name-defined]
    pd = pytest.importorskip("pandas")
    idx = pd.bdate_range(start, periods=days)
    return pd.DataFrame(
        {
            "open":   [1.0] * days,
            "high":   [1.1] * days,
            "low":    [0.9] * days,
            "close":  [1.0] * days,
            "volume": [1000.0] * days,
            "factor": [1.0] * days,
            "vwap":   [1.0] * days,
        },
        index=idx,
    ).astype("float32")


def test_dump_writes_calendar_and_instruments(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    frames = {tic: _fake_frame() for tic in CRYPTO_BASKET.instruments}
    dump_to_qlib(tmp_path, frames, CRYPTO_BASKET)
    assert (tmp_path / "calendars" / "day.txt").exists()
    assert (tmp_path / "instruments" / "all.txt").exists()
    assert (tmp_path / "instruments" / "crypto.txt").exists()
    for tic in CRYPTO_BASKET.instruments:
        for field in QLIB_FIELDS:
            assert (tmp_path / "features" / tic.lower() / f"{field}.day.bin").exists()


def test_bin_file_has_uint32_header_and_float32_payload(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    frames = {"BTC-USD": _fake_frame(days=4)}
    dump_to_qlib(tmp_path, frames, CRYPTO_BASKET)
    raw = (tmp_path / "features" / "btc-usd" / "close.day.bin").read_bytes()
    start_idx = struct.unpack("<I", raw[:4])[0]
    payload = raw[4:]
    assert start_idx == 0
    assert len(payload) == 4 * 4  # 4 days * float32


def test_verify_reports_complete_store(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    frames = {tic: _fake_frame() for tic in CRYPTO_BASKET.instruments}
    dump_to_qlib(tmp_path, frames, CRYPTO_BASKET)
    report = verify_store(tmp_path, CRYPTO_BASKET)
    assert isinstance(report, VerifyReport)
    assert report.ok
    assert report.n_instruments_found == 3
    assert report.calendar_days == 5
    assert not report.instruments_missing


def test_verify_flags_missing_instrument(tmp_path: Path) -> None:
    pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    frames = {tic: _fake_frame() for tic in list(CRYPTO_BASKET.instruments)[:2]}
    dump_to_qlib(tmp_path, frames, CRYPTO_BASKET)
    report = verify_store(tmp_path, CRYPTO_BASKET)
    assert not report.ok
    assert "sol-usd" in report.instruments_missing


def test_verify_raises_when_store_absent(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        verify_store(tmp_path / "nope", CRYPTO_BASKET)
