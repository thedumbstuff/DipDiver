"""Tests for the M6.3 veto-regret writer (compute_counterfactual + backfill job)."""

from __future__ import annotations

from datetime import date as date_cls, timedelta
from pathlib import Path

import pytest

from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    OrderSummary,
    VetoOutcomeEvent,
    append_event,
    read_events,
    utc_now_iso,
)
from dipdiver.harness.veto_regret import (
    DictPriceProvider,
    compute_counterfactual,
    set_provider_factory,
)


# ---------------------------------------------------------------------------
# DictPriceProvider — walk-back behaviour
# ---------------------------------------------------------------------------


def test_dict_provider_returns_exact_match():
    prov = DictPriceProvider(prices={("AAPL", "2026-06-04"): 200.0})
    result = prov.close_on_or_before("AAPL", date_cls(2026, 6, 4))
    assert result is not None
    actual_date, price = result
    assert actual_date == date_cls(2026, 6, 4)
    assert price == 200.0


def test_dict_provider_walks_back_to_last_available():
    """If target date is a weekend, the prior Friday close should be used."""
    prov = DictPriceProvider(prices={("AAPL", "2026-06-05"): 199.0})
    # 2026-06-07 is Sunday; should walk back to Friday 06-05
    result = prov.close_on_or_before("AAPL", date_cls(2026, 6, 7))
    assert result is not None
    actual_date, price = result
    assert actual_date == date_cls(2026, 6, 5)
    assert price == 199.0


def test_dict_provider_returns_none_when_symbol_missing():
    prov = DictPriceProvider(prices={("AAPL", "2026-06-05"): 199.0})
    assert prov.close_on_or_before("MSFT", date_cls(2026, 6, 5)) is None


def test_dict_provider_returns_none_beyond_walkback_window():
    """Walk-back stops after 7 days."""
    prov = DictPriceProvider(prices={("AAPL", "2026-05-01"): 180.0})
    assert prov.close_on_or_before("AAPL", date_cls(2026, 6, 5)) is None


# ---------------------------------------------------------------------------
# compute_counterfactual
# ---------------------------------------------------------------------------


def test_counterfactual_positive_when_settle_higher():
    """Vetoed buy that would have made money → positive pnl_pct."""
    prov = DictPriceProvider(prices={
        ("KO", "2026-06-01"): 60.0,
        ("KO", "2026-06-06"): 63.0,
    })
    result = compute_counterfactual(
        symbol="KO",
        entry_date=date_cls(2026, 6, 1),
        holding_window_days=5,
        provider=prov,
        today_utc=date_cls(2026, 6, 7),
    )
    assert result is not None
    assert result.counterfactual_pnl_pct == pytest.approx(0.05)
    assert result.estimated_entry_price == 60.0
    assert result.actual_price_at_settle == 63.0


def test_counterfactual_negative_when_settle_lower():
    """Vetoed buy that would have lost money → negative pnl_pct (committee saved us)."""
    prov = DictPriceProvider(prices={
        ("XOM", "2026-06-01"): 100.0,
        ("XOM", "2026-06-06"): 95.0,
    })
    result = compute_counterfactual(
        symbol="XOM",
        entry_date=date_cls(2026, 6, 1),
        holding_window_days=5,
        provider=prov,
        today_utc=date_cls(2026, 6, 7),
    )
    assert result is not None
    assert result.counterfactual_pnl_pct == pytest.approx(-0.05)


def test_counterfactual_none_when_settle_in_future():
    prov = DictPriceProvider(prices={
        ("KO", "2026-06-05"): 60.0,
    })
    result = compute_counterfactual(
        symbol="KO",
        entry_date=date_cls(2026, 6, 5),
        holding_window_days=5,
        provider=prov,
        today_utc=date_cls(2026, 6, 7),  # only 2 days later
    )
    assert result is None


def test_counterfactual_none_when_entry_price_missing():
    prov = DictPriceProvider(prices={("AAPL", "2026-06-06"): 200.0})
    result = compute_counterfactual(
        symbol="AAPL",
        entry_date=date_cls(2026, 5, 15),
        holding_window_days=5,
        provider=prov,
        today_utc=date_cls(2026, 6, 7),
    )
    assert result is None  # entry close not within walk-back window


# ---------------------------------------------------------------------------
# veto_backfill.run integration
# ---------------------------------------------------------------------------


def _seed_vetoed_buy(sb: Path, entry_date: str, symbol: str = "KO") -> None:
    """Seed one day_submitted with a vetoed buy."""
    event = DaySubmittedEvent(
        date=entry_date, universe="dow30",
        strategy_id="dow30_lightgbm_committee",
        timestamp_utc=utc_now_iso(),
        adds=[symbol],
        committee_active=True,
        committee_verdicts=[
            CommitteeVerdictSummary(
                symbol=symbol, direction="buy", approved=False,
                n_approve=1, n_veto=3, n_annotate=0,
                summary_rationale="risk veto", cost_usd=0.001,
            )
        ],
        orders_submitted=[],  # vetoed → no order
    )
    append_event(event, sb)


def test_veto_backfill_writes_outcome_event(data_root: Path, monkeypatch):
    """End-to-end: seed a veto, run backfill, expect one outcome event."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import veto_backfill as job

    sb = ui_scoreboard_path()
    _seed_vetoed_buy(sb, "2026-06-01", "KO")

    fake = DictPriceProvider(prices={
        ("KO", "2026-06-01"): 60.0,
        ("KO", "2026-06-06"): 62.4,
    })
    set_provider_factory(lambda: fake)
    monkeypatch.setattr(
        job, "_today_utc", lambda: date_cls(2026, 6, 7),
    )
    try:
        result = job.run()
    finally:
        set_provider_factory(None)

    assert result["events_written"] == 1
    veto_events = [e for e in read_events(sb) if e.event_type == "veto_outcome"]
    assert len(veto_events) == 1
    assert veto_events[0].symbol == "KO"
    assert veto_events[0].counterfactual_pnl_pct == pytest.approx(0.04)


def test_veto_backfill_is_per_symbol_idempotent(data_root: Path, monkeypatch):
    """Re-running adds zero duplicates even if multiple symbols share key tuple."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import veto_backfill as job

    sb = ui_scoreboard_path()
    # Two vetoed symbols on the same day
    event = DaySubmittedEvent(
        date="2026-06-01", universe="dow30",
        strategy_id="dow30_lightgbm_committee",
        timestamp_utc=utc_now_iso(),
        adds=["KO", "PG"],
        committee_active=True,
        committee_verdicts=[
            CommitteeVerdictSummary(
                symbol="KO", direction="buy", approved=False,
                n_approve=1, n_veto=3, n_annotate=0,
                summary_rationale="risk", cost_usd=0.001,
            ),
            CommitteeVerdictSummary(
                symbol="PG", direction="buy", approved=False,
                n_approve=1, n_veto=3, n_annotate=0,
                summary_rationale="risk", cost_usd=0.001,
            ),
        ],
    )
    append_event(event, sb)

    fake = DictPriceProvider(prices={
        ("KO", "2026-06-01"): 60.0, ("KO", "2026-06-06"): 62.0,
        ("PG", "2026-06-01"): 150.0, ("PG", "2026-06-06"): 153.0,
    })
    set_provider_factory(lambda: fake)
    monkeypatch.setattr(job, "_today_utc", lambda: date_cls(2026, 6, 7))
    try:
        r1 = job.run()
        r2 = job.run()
    finally:
        set_provider_factory(None)

    assert r1["events_written"] == 2
    assert r2["events_written"] == 0
    assert r2["already_present"] == 2
    veto_events = [e for e in read_events(sb) if e.event_type == "veto_outcome"]
    assert len(veto_events) == 2
    assert {e.symbol for e in veto_events} == {"KO", "PG"}


def test_veto_backfill_skips_unsettled_window(data_root: Path, monkeypatch):
    """A veto from 2 days ago has not yet reached T+5 → skipped."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import veto_backfill as job

    sb = ui_scoreboard_path()
    _seed_vetoed_buy(sb, "2026-06-05", "KO")
    set_provider_factory(lambda: DictPriceProvider(prices={
        ("KO", "2026-06-05"): 60.0,
    }))
    monkeypatch.setattr(job, "_today_utc", lambda: date_cls(2026, 6, 7))
    try:
        result = job.run()
    finally:
        set_provider_factory(None)

    assert result["events_written"] == 0
    assert result["candidates"] == 0


def test_veto_backfill_skips_approved_buys(data_root: Path, monkeypatch):
    """Approved buys aren't vetos → no counterfactual."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui.jobs import veto_backfill as job

    sb = ui_scoreboard_path()
    event = DaySubmittedEvent(
        date="2026-06-01", universe="dow30",
        strategy_id="dow30_lightgbm_committee",
        timestamp_utc=utc_now_iso(),
        adds=["KO"],
        committee_active=True,
        committee_verdicts=[
            CommitteeVerdictSummary(
                symbol="KO", direction="buy", approved=True,
                n_approve=4, n_veto=0, n_annotate=0,
                summary_rationale="ok", cost_usd=0.001,
            )
        ],
    )
    append_event(event, sb)

    set_provider_factory(lambda: DictPriceProvider(prices={
        ("KO", "2026-06-01"): 60.0, ("KO", "2026-06-06"): 62.0,
    }))
    monkeypatch.setattr(job, "_today_utc", lambda: date_cls(2026, 6, 7))
    try:
        result = job.run()
    finally:
        set_provider_factory(None)

    assert result["events_written"] == 0
    assert result["candidates"] == 0
