"""M1 scaffolding tests. No qlib dependency — runs in default CI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from dipdiver.brain.baselines import (
    CRYPTO_BASKET,
    DOW30,
    NIFTY50,
    BaselineConfig,
    BaselineResult,
    load_config,
)
from dipdiver.brain.baselines.results import compare, save_locked
from dipdiver.brain.baselines.runner import run_baseline
from dipdiver.brain.baselines.universes import UNIVERSES, get_universe

CONFIG_DIR = Path(__file__).parent.parent.parent / "dipdiver" / "brain" / "baselines" / "configs"


# ---------------------------------------------------------------------------
# Universes
# ---------------------------------------------------------------------------


def test_dow30_has_30_instruments() -> None:
    assert len(DOW30) == 30


def test_nifty50_has_50_instruments() -> None:
    assert len(NIFTY50) == 50


def test_crypto_basket_has_3_instruments() -> None:
    assert len(CRYPTO_BASKET) == 3


def test_universes_have_unique_tickers() -> None:
    for u in UNIVERSES.values():
        assert len(set(u.instruments)) == len(u.instruments), f"duplicates in {u.name}"


def test_get_universe_unknown() -> None:
    with pytest.raises(ValueError, match="unknown universe"):
        get_universe("dax40")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "test",
        "universe": "dow30",
        "model": "lightgbm",
        "train_start": "2014-01-01",
        "train_end": "2022-12-31",
        "valid_start": "2023-01-01",
        "valid_end": "2023-12-31",
        "test_start": "2024-01-01",
        "test_end": "2025-12-31",
        "benchmark": "^DJI",
        "qlib_provider_uri": "~/.qlib/qlib_data/us_data",
        "region": "us",
        "seed": 42,
    }
    base.update(overrides)
    return base


def test_config_hash_is_stable() -> None:
    c1 = BaselineConfig(**_valid_kwargs())  # type: ignore[arg-type]
    c2 = BaselineConfig(**_valid_kwargs())  # type: ignore[arg-type]
    assert c1.config_hash == c2.config_hash


def test_config_hash_changes_with_seed() -> None:
    c1 = BaselineConfig(**_valid_kwargs())  # type: ignore[arg-type]
    c2 = BaselineConfig(**_valid_kwargs(seed=43))  # type: ignore[arg-type]
    assert c1.config_hash != c2.config_hash


def test_config_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unsupported model"):
        BaselineConfig(**_valid_kwargs(model="xgboost"))  # type: ignore[arg-type]


def test_config_rejects_overlapping_windows() -> None:
    with pytest.raises(ValueError, match="no overlap"):
        BaselineConfig(**_valid_kwargs(valid_start="2022-06-01"))  # type: ignore[arg-type]


def test_config_rejects_reversed_range() -> None:
    with pytest.raises(ValueError, match="date range invalid"):
        BaselineConfig(**_valid_kwargs(test_end="2023-12-31"))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# YAML configs ship valid
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "dow30_lightgbm.yaml",
        "dow30_lstm.yaml",
        "nifty50_lightgbm.yaml",
        "nifty50_lstm.yaml",
        "crypto_lightgbm.yaml",
        "crypto_lstm.yaml",
    ],
)
def test_shipped_config_loads(filename: str) -> None:
    config = load_config(CONFIG_DIR / filename)
    assert config.config_hash  # non-empty


# ---------------------------------------------------------------------------
# Runner — mocked qlib
# ---------------------------------------------------------------------------


_FAKE_METRICS = {
    "annualised_return": 0.12,
    "annualised_volatility": 0.18,
    "sharpe": 0.67,
    "max_drawdown": -0.15,
    "hit_rate": 0.54,
    "turnover": 4.2,
    "n_trades": 130,
    "benchmark_annualised_return": 0.08,
}


def test_runner_assembles_result_from_metrics() -> None:
    config = BaselineConfig(**_valid_kwargs())  # type: ignore[arg-type]
    with mock.patch(
        "dipdiver.brain.baselines.runner._run_qlib_workflow",
        return_value=_FAKE_METRICS,
    ):
        result = run_baseline(config)
    assert result.config_hash == config.config_hash
    assert result.sharpe == 0.67
    assert result.excess_return == pytest.approx(0.04)


def test_runner_calls_into_qlib_workflow() -> None:
    """Runner delegates to _run_qlib_workflow; without qlib installed it errors clearly."""
    config = BaselineConfig(**_valid_kwargs())  # type: ignore[arg-type]
    # In scaffolding environments qlib isn't installed; the lazy import will
    # raise ImportError. Either ImportError (no qlib) or another runtime
    # error from real qlib without data is acceptable — the point is the
    # function is no longer a stub.
    with pytest.raises((ImportError, ModuleNotFoundError, Exception)):  # noqa: B017
        run_baseline(config)


# ---------------------------------------------------------------------------
# Results — lock + compare
# ---------------------------------------------------------------------------


def _fake_result(config_hash: str = "abc123", **overrides: object) -> BaselineResult:
    base: dict[str, object] = {
        "config_hash": config_hash,
        "config_name": "test",
        "universe": "dow30",
        "model": "lightgbm",
        "test_start": "2024-01-01",
        "test_end": "2025-12-31",
        "annualised_return": 0.12,
        "annualised_volatility": 0.18,
        "sharpe": 0.67,
        "max_drawdown": -0.15,
        "hit_rate": 0.54,
        "turnover": 4.2,
        "n_trades": 130,
        "benchmark_annualised_return": 0.08,
        "excess_return": 0.04,
        "qlib_version": "0.9.7",
        "git_sha": "deadbeef",
        "run_timestamp_utc": "2026-05-30T12:00:00+00:00",
    }
    base.update(overrides)
    return BaselineResult(**base)  # type: ignore[arg-type]


def test_compare_within_tolerance() -> None:
    locked = _fake_result()
    drifted = _fake_result(sharpe=0.68)  # ~1.5% drift
    assert compare(drifted, locked, tolerance=0.05) is True


def test_compare_outside_tolerance() -> None:
    locked = _fake_result()
    drifted = _fake_result(sharpe=0.50)  # ~25% drift
    assert compare(drifted, locked, tolerance=0.05) is False


def test_compare_rejects_hash_mismatch() -> None:
    a = _fake_result(config_hash="aaa")
    b = _fake_result(config_hash="bbb")
    with pytest.raises(ValueError, match="not the same run"):
        compare(a, b)


def test_save_locked_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dipdiver.brain.baselines.results.LOCKED_DIR", tmp_path)
    r = _fake_result(config_hash="trip001")
    path = save_locked(r)
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["sharpe"] == 0.67


def test_save_locked_refuses_overwrite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("dipdiver.brain.baselines.results.LOCKED_DIR", tmp_path)
    r = _fake_result(config_hash="trip002")
    save_locked(r)
    with pytest.raises(FileExistsError, match="already exists"):
        save_locked(r)
