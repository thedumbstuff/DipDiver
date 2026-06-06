"""Tests for the Stage 4 / M11 live-trading safety gate."""

from __future__ import annotations

from datetime import date as date_cls, timedelta
from pathlib import Path

import pytest

from dipdiver.adapters.alpaca.gate import (
    LiveModeNotAllowedError,
    LiveTradingGate,
    SUPPORTED_UNIVERSES,
)
from dipdiver.harness.scoreboard import (
    DaySubmittedEvent,
    OrderSummary,
    PnlSettledEvent,
    append_event,
    utc_now_iso,
)


def _seed_days(
    sb: Path, strategy_id: str, universe: str, n_days: int,
    *, win_rate: float = 1.0, daily_pnl: float = 100.0,
    equity_start: float = 100_000.0,
) -> None:
    """Write n_days of (day_submitted, pnl_settled) pairs to scoreboard."""
    eq = equity_start
    today = date_cls.today()
    for i in range(n_days):
        d = (today - timedelta(days=n_days - i)).isoformat()
        append_event(DaySubmittedEvent(
            date=d, universe=universe, strategy_id=strategy_id,
            timestamp_utc=utc_now_iso(),
            adds=["AAPL"],
            orders_submitted=[OrderSummary(
                symbol="AAPL", side="buy", notional_usd=5000.0,
                order_id=f"oid-{d}", submitted_at_utc=f"{d}T13:30:00Z",
            )],
        ), sb)
        pnl = daily_pnl if (i / max(1, n_days)) < win_rate else -abs(daily_pnl)
        eq += pnl
        append_event(PnlSettledEvent(
            date=d, universe=universe, strategy_id=strategy_id,
            timestamp_utc=utc_now_iso(),
            realised_pnl_usd=pnl,
            unrealised_pnl_usd=0.0,
            equity_at_close=eq,
            holdings_at_close={},
        ), sb)


def test_gate_fails_for_unsupported_universe(data_root: Path):
    from dipdiver._paths import ui_scoreboard_path
    sb = ui_scoreboard_path()
    _seed_days(sb, "world_indices_lightgbm", "world_indices", n_days=100,
               win_rate=1.0, daily_pnl=200.0)

    result = LiveTradingGate("world_indices_lightgbm").check()
    assert not result.passed
    universe_crit = next(c for c in result.criteria if c.name == "universe")
    assert not universe_crit.passed
    assert "world_indices" in str(universe_crit.actual)


def test_gate_fails_with_too_few_eval_days(data_root: Path):
    from dipdiver._paths import ui_scoreboard_path
    sb = ui_scoreboard_path()
    _seed_days(sb, "dow30_lightgbm", "dow30", n_days=10,
               win_rate=1.0, daily_pnl=100.0)

    result = LiveTradingGate("dow30_lightgbm").check()
    eval_crit = next(c for c in result.criteria if c.name == "forward_eval_days")
    assert not eval_crit.passed
    assert eval_crit.actual == 10


def test_gate_passes_when_all_criteria_met(data_root: Path):
    """Lower thresholds so synthetic data passes deterministically."""
    from dipdiver._paths import ui_scoreboard_path
    sb = ui_scoreboard_path()
    _seed_days(sb, "dow30_lightgbm", "dow30", n_days=80,
               win_rate=1.0, daily_pnl=200.0)

    gate = LiveTradingGate("dow30_lightgbm", thresholds={
        "forward_eval_days_min": 60,
        "sharpe_min": 0.0,  # synthetic data has zero variance → sharpe=0
        "max_dd_max": 0.50,
        "hit_rate_min": 0.50,
    })
    result = gate.check()
    assert result.passed, [
        (c.name, c.actual, c.threshold, c.passed) for c in result.criteria
    ]


def test_supported_universes_includes_dow30_and_sp500():
    assert "dow30" in SUPPORTED_UNIVERSES
    assert "sp500" in SUPPORTED_UNIVERSES
    assert "world_indices" not in SUPPORTED_UNIVERSES


# ---------------------------------------------------------------------------
# AlpacaPaperClient lockdown
# ---------------------------------------------------------------------------


def test_live_mode_rejects_without_strategy_id(data_root: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_API_SECRET", "y")
    monkeypatch.setenv("DIPDIVER_LIVE_TRADING", "true")
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient
    with pytest.raises(LiveModeNotAllowedError):
        AlpacaPaperClient(mode="live", strategy_id=None)


def test_live_mode_rejects_without_env_flag(data_root: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_API_SECRET", "y")
    monkeypatch.delenv("DIPDIVER_LIVE_TRADING", raising=False)
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient
    with pytest.raises(LiveModeNotAllowedError) as ei:
        AlpacaPaperClient(mode="live", strategy_id="dow30_lightgbm")
    assert ei.value.missing_env == "DIPDIVER_LIVE_TRADING=true"


def test_live_mode_rejects_when_gate_fails(data_root: Path, monkeypatch):
    """Env + strategy_id but no scoreboard history → gate fails."""
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_API_SECRET", "y")
    monkeypatch.setenv("DIPDIVER_LIVE_TRADING", "true")
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient
    with pytest.raises(LiveModeNotAllowedError) as ei:
        AlpacaPaperClient(mode="live", strategy_id="dow30_lightgbm")
    # The result should carry which criteria failed.
    assert not ei.value.result.passed
    names = {c.name for c in ei.value.result.criteria if not c.passed}
    assert "forward_eval_days" in names


def test_live_mode_rejects_bad_mode_string(data_root: Path, monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "x")
    monkeypatch.setenv("ALPACA_API_SECRET", "y")
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient
    with pytest.raises(ValueError):
        AlpacaPaperClient(mode="real-money-pls")


def test_audit_row_written_on_gate_check(data_root: Path):
    """LiveTradingGate.check() should write a LiveGateAudit row."""
    from dipdiver.ui import db
    db.init_db()
    LiveTradingGate("dow30_lightgbm").check()
    with db.session() as s:
        rows = s.query(db.LiveGateAudit).all()
    assert len(rows) == 1
    assert rows[0].strategy_id == "dow30_lightgbm"
