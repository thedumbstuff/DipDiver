"""Tests for the M6.2 P&L settlement writer."""

from __future__ import annotations

from datetime import date as date_cls, timedelta
from pathlib import Path

import pytest

from dipdiver.adapters.alpaca.portfolio import (
    DailyPnlSnapshot,
    StrategyShare,
    attribute_strategies,
)
from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    OrderSummary,
    PnlSettledEvent,
    append_event,
    read_events,
    utc_now_iso,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _day_event(
    *,
    date: str,
    universe: str = "dow30",
    strategy_id: str = "dow30_lightgbm",
    notionals: list[tuple[str, float]] | None = None,
) -> DaySubmittedEvent:
    notionals = notionals or [("AAPL", 5000.0), ("AMZN", 5000.0)]
    return DaySubmittedEvent(
        date=date, universe=universe, strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        adds=[s for s, _ in notionals],
        orders_submitted=[
            OrderSummary(
                symbol=s, side="buy", notional_usd=n,
                order_id=f"oid-{s}-{date}", submitted_at_utc=f"{date}T13:30:00Z",
            )
            for s, n in notionals
        ],
    )


def _seed_yesterday_submission(sb: Path, today: date_cls) -> str:
    yesterday = (today - timedelta(days=1)).isoformat()
    append_event(_day_event(date=yesterday), sb)
    return yesterday


def _seed_attribution_pair(sb: Path, date: str) -> None:
    """Two strategies submitting on the same date — one $20k, one $4k."""
    append_event(_day_event(
        date=date, strategy_id="dow30_lightgbm",
        notionals=[("AAPL", 10000.0), ("MSFT", 10000.0)],
    ), sb)
    append_event(_day_event(
        date=date, strategy_id="dow30_lightgbm_committee",
        notionals=[("KO", 2000.0), ("PG", 2000.0)],
    ), sb)


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


def test_attribute_strategies_single_strategy_gets_full_weight():
    e = _day_event(date="2026-06-04")
    shares = attribute_strategies([e])
    assert len(shares) == 1
    assert shares[0].weight == 1.0
    assert shares[0].attribution_method == "single_strategy"


def test_attribute_strategies_multi_strategy_normalises_by_notional():
    e1 = _day_event(date="2026-06-04", strategy_id="a",
                    notionals=[("AAPL", 9000.0)])
    e2 = _day_event(date="2026-06-04", strategy_id="b",
                    notionals=[("MSFT", 1000.0)])
    shares = {s.strategy_id: s for s in attribute_strategies([e1, e2])}
    assert shares["a"].weight == pytest.approx(0.9)
    assert shares["b"].weight == pytest.approx(0.1)
    assert shares["a"].attribution_method == "weighted_by_notional"


def test_attribute_strategies_zero_notional_splits_equally():
    e1 = DaySubmittedEvent(date="2026-06-04", universe="dow30",
                           strategy_id="a", timestamp_utc=utc_now_iso())
    e2 = DaySubmittedEvent(date="2026-06-04", universe="dow30",
                           strategy_id="b", timestamp_utc=utc_now_iso())
    shares = attribute_strategies([e1, e2])
    assert all(s.weight == pytest.approx(0.5) for s in shares)


# ---------------------------------------------------------------------------
# pnl_settle.run with a fake provider
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_provider_fixture():
    """Yields a (calls, fake_provider) tuple. calls tracks invocations."""

    calls: list[date_cls] = []

    def factory(realised: float = 120.0, unrealised: float = 30.0, equity: float = 10150.0):
        def _provider(target_date: date_cls) -> DailyPnlSnapshot:
            calls.append(target_date)
            return DailyPnlSnapshot(
                date=target_date.isoformat(),
                realised_pnl_usd=realised,
                unrealised_pnl_usd=unrealised,
                equity_at_close=equity,
                holdings_at_close={"AAPL": 5000.0, "AMZN": 5050.0},
            )
        return _provider

    return calls, factory


def test_pnl_settle_writes_one_event_per_strategy(
    data_root: Path, fake_provider_fixture,
):
    """A single submitted day → one pnl_settled event for that strategy."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import pnl_settle as job
    from dipdiver.ui.jobs.pnl_settle import _today_utc

    sb = ui_scoreboard_path()
    yesterday = _seed_yesterday_submission(sb, _today_utc())
    calls, factory = fake_provider_fixture
    job.set_provider_override(factory())

    try:
        result = job.run()
    finally:
        job.set_provider_override(None)

    assert result["rc"] == 0
    assert result["events_written"] == 1
    assert len(calls) == 1
    assert calls[0].isoformat() == yesterday

    events = read_events(sb)
    pnl_events = [e for e in events if e.event_type == "pnl_settled"]
    assert len(pnl_events) == 1
    assert pnl_events[0].date == yesterday
    assert pnl_events[0].attribution_method == "single_strategy"
    assert pnl_events[0].attribution_weight == 1.0
    assert pnl_events[0].realised_pnl_usd == 120.0
    assert pnl_events[0].source == "alpaca_portfolio_history"


def test_pnl_settle_is_idempotent(data_root: Path, fake_provider_fixture):
    """Re-running adds zero duplicates."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import pnl_settle as job
    from dipdiver.ui.jobs.pnl_settle import _today_utc

    sb = ui_scoreboard_path()
    _seed_yesterday_submission(sb, _today_utc())
    _, factory = fake_provider_fixture
    job.set_provider_override(factory())

    try:
        first = job.run()
        second = job.run()
    finally:
        job.set_provider_override(None)

    assert first["events_written"] == 1
    assert second["events_written"] == 0
    pnl_events = [e for e in read_events(sb) if e.event_type == "pnl_settled"]
    assert len(pnl_events) == 1


def test_pnl_settle_skips_today(data_root: Path, fake_provider_fixture):
    """Don't settle a day that hasn't ended yet."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import pnl_settle as job
    from dipdiver.ui.jobs.pnl_settle import _today_utc

    sb = ui_scoreboard_path()
    append_event(_day_event(date=_today_utc().isoformat()), sb)
    _, factory = fake_provider_fixture
    job.set_provider_override(factory())

    try:
        result = job.run()
    finally:
        job.set_provider_override(None)

    assert result["events_written"] == 0
    assert result["dates_processed"] == 0


def test_pnl_settle_multi_strategy_splits_by_notional(
    data_root: Path, fake_provider_fixture,
):
    """Two strategies on the same date → two events with weights summing to 1."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import pnl_settle as job
    from dipdiver.ui.jobs.pnl_settle import _today_utc

    sb = ui_scoreboard_path()
    yesterday = (_today_utc() - timedelta(days=1)).isoformat()
    _seed_attribution_pair(sb, yesterday)
    _, factory = fake_provider_fixture
    job.set_provider_override(factory(realised=240.0, unrealised=60.0, equity=20300.0))

    try:
        result = job.run()
    finally:
        job.set_provider_override(None)

    assert result["events_written"] == 2
    pnl_events = [e for e in read_events(sb) if e.event_type == "pnl_settled"]
    weights = {e.strategy_id: e.attribution_weight for e in pnl_events}
    # First strategy submitted $20k of $24k total ≈ 0.833
    # attribution_weight is rounded to 6 dp for audit readability — loosen tolerance.
    assert weights["dow30_lightgbm"] == pytest.approx(20000.0 / 24000.0, abs=1e-5)
    assert weights["dow30_lightgbm_committee"] == pytest.approx(4000.0 / 24000.0, abs=1e-5)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)
    # Realised P&L is scaled by weight
    pnl_main = next(e for e in pnl_events if e.strategy_id == "dow30_lightgbm")
    assert pnl_main.realised_pnl_usd == pytest.approx(240.0 * 20000.0 / 24000.0, rel=1e-3)


def test_pnl_settle_provider_error_returns_rc1_with_message(
    data_root: Path,
):
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import pnl_settle as job
    from dipdiver.ui.jobs.pnl_settle import _today_utc

    sb = ui_scoreboard_path()
    _seed_yesterday_submission(sb, _today_utc())

    def bad_provider(d):  # noqa: ARG001
        raise RuntimeError("alpaca down")

    job.set_provider_override(bad_provider)
    try:
        result = job.run()
    finally:
        job.set_provider_override(None)

    assert result["rc"] == 1
    assert result["events_written"] == 0
    assert "errors" in result
