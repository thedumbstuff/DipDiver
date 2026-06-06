"""Scoreboard event log tests.

The scoreboard is the project's audit chain — these tests are the safety net
that prevents append-only invariants from regressing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    OrderSummary,
    PnlSettledEvent,
    VetoOutcomeEvent,
    already_recorded,
    append_event,
    filter_events,
    read_events,
    utc_now_iso,
)


# ---------------------------------------------------------------------------
# Schema round-trip
# ---------------------------------------------------------------------------


def _make_day(date: str = "2026-06-04", strategy_id: str = "dow30_lightgbm_committee") -> DaySubmittedEvent:
    return DaySubmittedEvent(
        date=date, universe="dow30", strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        config_name="dow30_lightgbm.yaml",
        target_holdings=["AAPL", "CVX"],
        adds=["CVX"], removes=["AMGN"],
        committee_active=True,
        committee_verdicts=[CommitteeVerdictSummary(
            symbol="CVX", direction="buy", approved=True,
            n_approve=3, n_veto=0, n_annotate=1,
            summary_rationale="ok", cost_usd=0.001,
        )],
        orders_submitted=[OrderSummary(
            symbol="CVX", side="buy", notional_usd=9940.0,
            order_id="abc-123", submitted_at_utc="2026-06-04T01:45:21Z",
        )],
        account_equity_pre=99400.04,
    )


def test_day_submitted_round_trips(tmp_scoreboard: Path):
    """A DaySubmittedEvent written and re-read keeps every field."""
    e = _make_day()
    append_event(e, tmp_scoreboard)
    events = read_events(tmp_scoreboard)
    assert len(events) == 1
    out = events[0]
    assert isinstance(out, DaySubmittedEvent)
    assert out.date == e.date
    assert out.strategy_id == e.strategy_id
    assert len(out.committee_verdicts) == 1
    assert out.committee_verdicts[0].symbol == "CVX"
    assert out.orders_submitted[0].order_id == "abc-123"


def test_pnl_settled_round_trips(tmp_scoreboard: Path):
    e = PnlSettledEvent(
        date="2026-06-04", universe="dow30",
        strategy_id="dow30_lightgbm_committee",
        timestamp_utc=utc_now_iso(),
        realised_pnl_usd=125.5, unrealised_pnl_usd=-12.0,
        holdings_at_close={"AAPL": 50000.0}, equity_at_close=99525.5,
    )
    append_event(e, tmp_scoreboard)
    out = read_events(tmp_scoreboard)[0]
    assert isinstance(out, PnlSettledEvent)
    assert out.realised_pnl_usd == 125.5
    assert out.holdings_at_close == {"AAPL": 50000.0}


def test_veto_outcome_round_trips(tmp_scoreboard: Path):
    e = VetoOutcomeEvent(
        date="2026-06-04", universe="dow30",
        strategy_id="dow30_lightgbm_committee",
        timestamp_utc=utc_now_iso(),
        settle_date="2026-06-09", symbol="TSLA",
        estimated_entry_price=180.0, actual_price_at_settle=175.5,
        counterfactual_pnl_pct=-2.5, holding_window_days=5,
    )
    append_event(e, tmp_scoreboard)
    out = read_events(tmp_scoreboard)[0]
    assert isinstance(out, VetoOutcomeEvent)
    assert out.symbol == "TSLA"


def test_polymorphic_discriminator_picks_right_type(tmp_scoreboard: Path):
    """Three events of different types, read back as the right types."""
    append_event(_make_day(), tmp_scoreboard)
    append_event(PnlSettledEvent(
        date="2026-06-04", universe="dow30", strategy_id="x",
        timestamp_utc=utc_now_iso(),
        realised_pnl_usd=0.0, unrealised_pnl_usd=0.0,
        holdings_at_close={}, equity_at_close=100.0,
    ), tmp_scoreboard)
    append_event(VetoOutcomeEvent(
        date="2026-06-04", universe="dow30", strategy_id="x",
        timestamp_utc=utc_now_iso(),
        settle_date="2026-06-09", symbol="X",
        actual_price_at_settle=10.0, counterfactual_pnl_pct=0.0,
        holding_window_days=5,
    ), tmp_scoreboard)
    events = read_events(tmp_scoreboard)
    assert [type(e).__name__ for e in events] == [
        "DaySubmittedEvent", "PnlSettledEvent", "VetoOutcomeEvent",
    ]


# ---------------------------------------------------------------------------
# Append-only invariant
# ---------------------------------------------------------------------------


def test_append_preserves_existing_lines(tmp_scoreboard: Path):
    """append_event must not rewrite previous lines."""
    append_event(_make_day(date="2026-06-03"), tmp_scoreboard)
    first_bytes = tmp_scoreboard.read_bytes()
    append_event(_make_day(date="2026-06-04"), tmp_scoreboard)
    second_bytes = tmp_scoreboard.read_bytes()
    assert second_bytes.startswith(first_bytes), "append must preserve byte-prefix of old content"
    assert second_bytes.count(b"\n") == 2


def test_each_line_is_self_contained_json(tmp_scoreboard: Path):
    """Every line in the JSONL must parse as standalone JSON."""
    import json
    for d in ["2026-06-01", "2026-06-02", "2026-06-03"]:
        append_event(_make_day(date=d), tmp_scoreboard)
    for line in tmp_scoreboard.read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            assert "event_type" in obj
            assert "date" in obj


def test_read_skips_blank_lines(tmp_scoreboard: Path):
    """Manual blank lines (from editors etc.) don't break the reader."""
    append_event(_make_day(date="2026-06-01"), tmp_scoreboard)
    # Append a blank line manually
    with tmp_scoreboard.open("a", encoding="utf-8") as f:
        f.write("\n\n")
    append_event(_make_day(date="2026-06-02"), tmp_scoreboard)
    events = read_events(tmp_scoreboard)
    assert len(events) == 2


def test_read_nonexistent_returns_empty(tmp_scoreboard: Path):
    """No-throw on missing file (used by /scoreboard before any writes)."""
    assert read_events(tmp_scoreboard) == []


# ---------------------------------------------------------------------------
# Idempotence helpers
# ---------------------------------------------------------------------------


def test_already_recorded_matches_full_tuple(tmp_scoreboard: Path):
    append_event(_make_day(date="2026-06-04", strategy_id="x"), tmp_scoreboard)
    events = read_events(tmp_scoreboard)
    assert already_recorded(events, date="2026-06-04", universe="dow30",
                            strategy_id="x", event_type="day_submitted")
    # Different strategy → not recorded
    assert not already_recorded(events, date="2026-06-04", universe="dow30",
                                strategy_id="y", event_type="day_submitted")
    # Different event type → not recorded
    assert not already_recorded(events, date="2026-06-04", universe="dow30",
                                strategy_id="x", event_type="pnl_settled")


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_filter_events_by_strategy(tmp_scoreboard: Path):
    append_event(_make_day(date="2026-06-03", strategy_id="a"), tmp_scoreboard)
    append_event(_make_day(date="2026-06-04", strategy_id="b"), tmp_scoreboard)
    events = read_events(tmp_scoreboard)
    only_a = filter_events(events, strategy_id="a")
    assert len(only_a) == 1
    assert only_a[0].strategy_id == "a"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_invalid_decision_value_rejected():
    """AgentVerdict-style decisions are restricted to approve/veto/annotate
    by Pydantic — same validation applies to CommitteeVerdictSummary's direction."""
    with pytest.raises(ValidationError):
        CommitteeVerdictSummary(
            symbol="X", direction="hodl",  # type: ignore[arg-type]
            approved=True, n_approve=1, n_veto=0, n_annotate=0,
            summary_rationale="", cost_usd=0.0,
        )


def test_invalid_event_type_rejected():
    """If a stray event_type leaks into the JSONL, the discriminator should reject."""
    import json
    from pydantic import TypeAdapter
    from dipdiver.harness.scoreboard import ScoreboardEvent

    adapter = TypeAdapter(ScoreboardEvent)
    bad = json.dumps({"event_type": "made_up", "date": "2026-06-04",
                      "universe": "dow30", "strategy_id": "x",
                      "timestamp_utc": "now"})
    with pytest.raises((ValidationError, Exception)):
        adapter.validate_json(bad)
