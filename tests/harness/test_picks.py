"""Tests for the M9 picks data layer.

The /picks route layer is exercised separately in tests/ui/.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dipdiver.harness.picks import (
    EnrichedPick,
    Pick,
    enrich_with_committee,
    latest_signal_date,
    load_next_signal_forecast,
    merge_watchlist,
    signal_freshness_hours,
    size_by_risk_band,
    weight_pct_for_risk_band,
)
from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    OrderSummary,
    append_event,
    utc_now_iso,
)


def _write_signals(path: Path, rows: list[tuple[str, str, float]]) -> None:
    """rows = [(date, symbol, score), ...]"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,symbol,score"]
    for d, s, sc in rows:
        lines.append(f"{d},{s},{sc}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CSV loader
# ---------------------------------------------------------------------------


def test_load_next_signal_returns_top_n_sorted_desc(tmp_path: Path):
    csv_p = tmp_path / "dow30_lightgbm.csv"
    _write_signals(csv_p, [
        ("2026-06-04", "AAPL", 0.07),
        ("2026-06-04", "AMZN", 0.10),
        ("2026-06-04", "MSFT", 0.05),
        ("2026-06-04", "CVX", 0.03),
        ("2026-06-03", "AAPL", 0.99),  # older — must be excluded
    ])
    picks = load_next_signal_forecast("dow30_lightgbm", csv_path=csv_p, top_n=3)
    assert [p.symbol for p in picks] == ["AMZN", "AAPL", "MSFT"]
    assert all(p.signal_date == "2026-06-04" for p in picks)
    assert picks[0].rank == 1


def test_load_next_signal_empty_when_file_missing(tmp_path: Path):
    csv_p = tmp_path / "nope.csv"
    assert load_next_signal_forecast("nope", csv_path=csv_p) == []


def test_latest_signal_date_picks_max(tmp_path: Path):
    csv_p = tmp_path / "x.csv"
    _write_signals(csv_p, [
        ("2026-06-03", "A", 0.1),
        ("2026-06-04", "A", 0.2),
        ("2026-06-02", "A", 0.0),
    ])
    assert latest_signal_date(csv_p) == "2026-06-04"


def test_signal_freshness_hours(tmp_path: Path):
    csv_p = tmp_path / "x.csv"
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    _write_signals(csv_p, [(yesterday, "AAPL", 0.1)])
    h = signal_freshness_hours(csv_p)
    assert h is not None
    assert 24.0 - 1.0 < h < 24.0 + 12.0  # tolerant — date arith vs now


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def test_weight_pct_per_risk_band():
    assert weight_pct_for_risk_band("aggressive") == 5.0
    assert weight_pct_for_risk_band("balanced") == 3.0
    assert weight_pct_for_risk_band("conservative") == 1.0
    assert weight_pct_for_risk_band("gibberish") == 3.0  # falls back to balanced


def test_size_by_risk_band_applies_uniform_weight():
    picks = [EnrichedPick(rank=i, symbol=s, score=0.1, signal_date="2026-06-04",
                          universe="dow30")
             for i, s in enumerate(("AAPL", "AMZN"), start=1)]
    out = size_by_risk_band(picks, "aggressive")
    assert all(p.weight_pct == 5.0 for p in out)


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


def _seed_day_event(sb_path: Path, *, date: str, strategy_id: str,
                    verdicts: list[tuple[str, bool, int, int]]):
    """verdicts = [(symbol, approved, n_approve, n_veto), ...]"""
    decisions = [
        CommitteeVerdictSummary(
            symbol=s, direction="buy", approved=a,
            n_approve=na, n_veto=nv, n_annotate=0,
            summary_rationale=f"why-{s}", cost_usd=0.001,
        )
        for s, a, na, nv in verdicts
    ]
    e = DaySubmittedEvent(
        date=date, universe="dow30", strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        committee_active=True,
        committee_verdicts=decisions,
    )
    append_event(e, sb_path)


def test_enrich_attaches_decision_and_rationale(data_root: Path):
    from dipdiver._paths import ui_scoreboard_path
    sb = ui_scoreboard_path()
    _seed_day_event(sb, date="2026-06-04", strategy_id="dow30_lightgbm_committee",
                    verdicts=[("AAPL", True, 4, 0), ("AMZN", False, 1, 3)])

    raw = [
        Pick(rank=1, symbol="AAPL", score=0.1, signal_date="2026-06-04"),
        Pick(rank=2, symbol="AMZN", score=0.08, signal_date="2026-06-04"),
        Pick(rank=3, symbol="MSFT", score=0.06, signal_date="2026-06-04"),
    ]
    from dipdiver.harness.scoreboard import read_events
    enriched = enrich_with_committee(
        raw, universe="dow30", strategy_id="dow30_lightgbm_committee",
        events=read_events(sb),
    )
    by_sym = {p.symbol: p for p in enriched}
    assert by_sym["AAPL"].decision == "approved"
    assert by_sym["AAPL"].summary_rationale == "why-AAPL"
    assert by_sym["AMZN"].decision == "vetoed"
    # MSFT had no committee verdict
    assert by_sym["MSFT"].decision is None
    assert by_sym["MSFT"].summary_rationale is None


def test_enrich_returns_raw_picks_when_no_strategy(data_root: Path):
    raw = [Pick(rank=1, symbol="AAPL", score=0.1, signal_date="2026-06-04")]
    enriched = enrich_with_committee(raw, universe="dow30", strategy_id=None, events=[])
    assert len(enriched) == 1
    assert enriched[0].decision is None


def test_merge_watchlist_appends_missing_symbols(data_root: Path):
    from dipdiver.ui import db
    db.init_db()
    with db.session() as s:
        s.add(db.WatchlistEntry(
            symbol="GE", universe="dow30", notes="long shot",
            added_utc=datetime.now(timezone.utc),
        ))
    picks = [EnrichedPick(rank=1, symbol="AAPL", score=0.1,
                          signal_date="2026-06-04", universe="dow30")]
    merged = merge_watchlist(picks, universe="dow30", top_n=10)
    symbols = [p.symbol for p in merged]
    assert "GE" in symbols
    assert any(p.symbol == "GE" and p.on_watchlist for p in merged)


def test_merge_watchlist_marks_existing_when_already_present(data_root: Path):
    from dipdiver.ui import db
    db.init_db()
    with db.session() as s:
        s.add(db.WatchlistEntry(
            symbol="AAPL", universe="dow30", notes="",
            added_utc=datetime.now(timezone.utc),
        ))
    picks = [EnrichedPick(rank=1, symbol="AAPL", score=0.1,
                          signal_date="2026-06-04", universe="dow30")]
    merged = merge_watchlist(picks, universe="dow30", top_n=10)
    assert merged[0].on_watchlist is True
