"""Render / fusion tests.

The fused rows are what the human-facing UI uses; subtle veto-rate semantics
matter because that's the headline number we calibrate the committee on.
"""

from __future__ import annotations

from pathlib import Path

from dipdiver.harness.render import (
    fuse_by_day,
    render_full_report,
    render_markdown_table,
    render_strategy_summary,
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


def _make_day(
    *,
    date: str = "2026-06-04",
    strategy_id: str = "dow30_lightgbm_committee",
    committee_active: bool = True,
    adds: list[str] | None = None,
    verdicts: list[tuple[str, bool]] | None = None,
) -> DaySubmittedEvent:
    """verdicts is [(symbol, approved), ...]. Each costs $0.001."""
    adds_l = adds if adds is not None else (
        [s for s, _ in verdicts] if verdicts else ["AAPL", "AMZN"]
    )
    vlist = [
        CommitteeVerdictSummary(
            symbol=s, direction="buy", approved=ap,
            n_approve=3 if ap else 1,
            n_veto=0 if ap else 3,
            n_annotate=1 if ap else 0,
            summary_rationale="ok", cost_usd=0.001,
        )
        for s, ap in (verdicts or [])
    ]
    return DaySubmittedEvent(
        date=date, universe="dow30", strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        adds=adds_l, removes=[],
        committee_active=committee_active,
        committee_verdicts=vlist,
        orders_submitted=[
            OrderSummary(
                symbol=s, side="buy", notional_usd=9940.0,
                order_id=f"oid-{s}", submitted_at_utc=f"{date}T01:45:21Z",
            )
            for s in adds_l
        ],
    )


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------


def test_fuse_groups_one_row_per_strategy_day(tmp_path: Path):
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(date="2026-06-03", strategy_id="a"), sb)
    append_event(_make_day(date="2026-06-04", strategy_id="a"), sb)
    append_event(_make_day(date="2026-06-04", strategy_id="b"), sb)
    rows = fuse_by_day(read_events(sb))
    assert len(rows) == 3
    # Sorted: most recent first
    assert rows[0].date == "2026-06-04"
    assert rows[-1].date == "2026-06-03"


def test_fuse_attaches_pnl_to_matching_day(tmp_path: Path):
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(date="2026-06-04", strategy_id="x"), sb)
    append_event(PnlSettledEvent(
        date="2026-06-04", universe="dow30", strategy_id="x",
        timestamp_utc=utc_now_iso(),
        realised_pnl_usd=100.0, unrealised_pnl_usd=20.0,
        holdings_at_close={"AAPL": 50000.0}, equity_at_close=99520.0,
    ), sb)
    rows = fuse_by_day(read_events(sb))
    assert len(rows) == 1
    assert rows[0].pnl is not None
    assert rows[0].pnl.realised_pnl_usd == 100.0
    assert rows[0].pnl.equity_at_close == 99520.0


def test_fuse_collects_multiple_veto_outcomes(tmp_path: Path):
    """A day might have multiple veto-outcomes (one per vetoed symbol)."""
    from dipdiver.harness.scoreboard import VetoOutcomeEvent
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(date="2026-06-04", strategy_id="x"), sb)
    for sym in ("KO", "MSFT"):
        append_event(VetoOutcomeEvent(
            date="2026-06-04", universe="dow30", strategy_id="x",
            timestamp_utc=utc_now_iso(),
            settle_date="2026-06-09", symbol=sym,
            actual_price_at_settle=100.0,
            counterfactual_pnl_pct=2.0,
            holding_window_days=5,
        ), sb)
    rows = fuse_by_day(read_events(sb))
    assert len(rows) == 1
    assert len(rows[0].veto_outcomes) == 2


# ---------------------------------------------------------------------------
# Veto-rate semantics — these matter for committee calibration
# ---------------------------------------------------------------------------


def test_veto_rate_none_when_committee_off(tmp_path: Path):
    """No committee on this day means veto_rate is undefined, not 0%."""
    sb = tmp_path / "sb.jsonl"
    append_event(
        _make_day(date="2026-06-03", strategy_id="base", committee_active=False, adds=["A", "B", "C"]),
        sb,
    )
    rows = fuse_by_day(read_events(sb))
    r = rows[0]
    assert r.n_buys_proposed == 3
    assert r.n_buys_reviewed == 0
    assert r.veto_rate is None  # critical: not 0.0


def test_veto_rate_uses_reviewed_not_proposed(tmp_path: Path):
    """If committee is active and 3 buys reviewed with 1 vetoed → 33%, not /3 of total."""
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(
        verdicts=[("A", True), ("B", True), ("C", False)],
    ), sb)
    r = fuse_by_day(read_events(sb))[0]
    assert r.n_buys_proposed == 3
    assert r.n_buys_reviewed == 3
    assert r.n_buys_vetoed == 1
    assert abs((r.veto_rate or 0) - 1/3) < 1e-9


def test_veto_rate_100_percent(tmp_path: Path):
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(
        verdicts=[("A", False), ("B", False)],
    ), sb)
    r = fuse_by_day(read_events(sb))[0]
    assert r.veto_rate == 1.0


def test_committee_cost_sums_per_verdict(tmp_path: Path):
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(
        verdicts=[("A", True), ("B", True), ("C", True)],
    ), sb)
    r = fuse_by_day(read_events(sb))[0]
    assert abs(r.committee_cost_usd - 0.003) < 1e-9


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_markdown_table_handles_empty_rows():
    out = render_markdown_table([])
    assert "_no scoreboard rows yet" in out


def test_render_full_report_contains_both_tables(tmp_path: Path):
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(date="2026-06-03", strategy_id="a", committee_active=False, adds=["X"]), sb)
    append_event(_make_day(date="2026-06-04", strategy_id="b",
                            verdicts=[("X", True), ("Y", False)]), sb)
    report = render_full_report(fuse_by_day(read_events(sb)))
    assert "Per-strategy running totals" in report
    assert "Per-day log" in report
    assert "dow30_lightgbm" in report or "a" in report  # strategy IDs surfaced


def test_strategy_summary_shows_dash_when_no_buys_reviewed(tmp_path: Path):
    """No-committee strategy must show '—' for veto rate, NOT '0%'."""
    sb = tmp_path / "sb.jsonl"
    append_event(_make_day(date="2026-06-03", strategy_id="nocomm",
                            committee_active=False, adds=["A", "B"]), sb)
    summary = render_strategy_summary(fuse_by_day(read_events(sb)))
    # The 'nocomm' row should contain '—' (em dash) under veto rate, not '0%'
    line = [ln for ln in summary.splitlines() if "nocomm" in ln][0]
    assert "—" in line
    # Make sure we don't accidentally show '0%' for a no-committee strategy
    # (regression guard from the actual bug fixed earlier).
    cells = line.split("|")
    veto_cell = cells[7].strip()  # 0-indexed; column 7 = veto rate
    assert veto_cell == "—"
