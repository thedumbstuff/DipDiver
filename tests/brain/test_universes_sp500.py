"""Tests for SP500 universe + Universe.live_executable flag (Stage 6 / M13)."""

from __future__ import annotations

from dipdiver.brain.baselines.universes import UNIVERSES, get_universe


def test_sp500_registered_in_universes_dict():
    assert "sp500" in UNIVERSES
    u = get_universe("sp500")
    assert u.region == "us"
    # Starter list has 60 hand-picked tickers
    assert len(u.instruments) >= 50


def test_sp500_is_live_executable_on_alpaca():
    u = get_universe("sp500")
    assert u.live_executable is True


def test_dow30_still_live_executable():
    u = get_universe("dow30")
    assert u.live_executable is True


def test_world_indices_marked_research_only():
    u = get_universe("world_indices")
    assert u.live_executable is False


def test_crypto_marked_research_only():
    u = get_universe("crypto")
    assert u.live_executable is False


def test_nifty50_marked_research_only():
    u = get_universe("nifty50")
    assert u.live_executable is False


def test_universe_has_symbols_alias():
    """registry_api uses .symbols — must be an alias for instruments."""
    u = get_universe("dow30")
    assert u.symbols == u.instruments


def test_sp500_yaml_configs_exist():
    """SP500 yaml configs should be importable via load_config."""
    from dipdiver._paths import repo_root
    from dipdiver.brain.baselines.config import load_config
    base = repo_root() / "dipdiver" / "brain" / "baselines" / "configs"
    for fn in ("sp500_lightgbm.yaml", "sp500_lstm.yaml"):
        cfg = load_config(base / fn)
        assert cfg.universe == "sp500"
        assert cfg.region == "us"
