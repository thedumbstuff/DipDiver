"""Fuse scoreboard events into a per-day summary table for human display.

The JSONL is the source of truth (append-only). This module collapses events
by (date, universe, strategy_id) into one logical row each, then renders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from dipdiver.harness.scoreboard import (
    DaySubmittedEvent,
    PnlSettledEvent,
    ScoreboardEvent,
    VetoOutcomeEvent,
)


@dataclass
class FusedDayRow:
    """One human-displayable line per (date, universe, strategy_id)."""

    date: str
    universe: str
    strategy_id: str
    submitted: DaySubmittedEvent | None = None
    pnl: PnlSettledEvent | None = None
    veto_outcomes: list[VetoOutcomeEvent] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.date, self.universe, self.strategy_id)

    @property
    def n_orders(self) -> int:
        return len(self.submitted.orders_submitted) if self.submitted else 0

    @property
    def n_buys_proposed(self) -> int:
        if not self.submitted:
            return 0
        return len(self.submitted.adds)

    @property
    def n_buys_reviewed(self) -> int:
        """Buys that actually went through the committee (0 if committee off)."""
        if not self.submitted or not self.submitted.committee_active:
            return 0
        return sum(1 for v in self.submitted.committee_verdicts if v.direction == "buy")

    @property
    def n_buys_vetoed(self) -> int:
        if not self.submitted:
            return 0
        return sum(
            1 for v in self.submitted.committee_verdicts
            if v.direction == "buy" and not v.approved
        )

    @property
    def veto_rate(self) -> float | None:
        if self.n_buys_reviewed == 0:
            return None
        return self.n_buys_vetoed / self.n_buys_reviewed

    @property
    def committee_cost_usd(self) -> float:
        if not self.submitted:
            return 0.0
        return sum(v.cost_usd for v in self.submitted.committee_verdicts)


def fuse_by_day(events: Iterable[ScoreboardEvent]) -> list[FusedDayRow]:
    """Collapse events into one row per (date, universe, strategy_id).

    If multiple events of the same type exist for one key, the LATEST one
    (by list order — i.e. write order in the JSONL) wins. This makes the
    fusion deterministic without requiring full timestamp comparison.
    """
    rows: dict[tuple[str, str, str], FusedDayRow] = {}
    for e in events:
        key = (e.date, e.universe, e.strategy_id)
        if key not in rows:
            rows[key] = FusedDayRow(date=e.date, universe=e.universe, strategy_id=e.strategy_id)
        row = rows[key]
        if isinstance(e, DaySubmittedEvent):
            row.submitted = e
        elif isinstance(e, PnlSettledEvent):
            row.pnl = e
        elif isinstance(e, VetoOutcomeEvent):
            row.veto_outcomes.append(e)
    # Sort: most recent date first; within a date, by strategy_id
    return sorted(rows.values(), key=lambda r: (r.date, r.universe, r.strategy_id), reverse=True)


def _fmt_pnl(value: float | None) -> str:
    if value is None:
        return "—"
    sign = "+" if value >= 0 else ""
    return f"{sign}${value:,.2f}"


def _fmt_rate(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.0%}"


def render_markdown_table(rows: list[FusedDayRow]) -> str:
    """Render a fused row list as a Github-flavored Markdown table.

    Optimized for at-a-glance reading: one line per strategy-day.
    """
    if not rows:
        return "_no scoreboard rows yet — run `python scripts/m6_backfill.py`_"

    out: list[str] = []
    out.append("| Date | Universe | Strategy | Orders | Buys | Vetoed | Veto rate | Realised P&L | Unrealised | Equity close |")
    out.append("|------|----------|----------|-------:|-----:|-------:|----------:|-------------:|-----------:|-------------:|")
    for r in rows:
        equity = r.pnl.equity_at_close if r.pnl else None
        rp = _fmt_pnl(r.pnl.realised_pnl_usd if r.pnl else None)
        up = _fmt_pnl(r.pnl.unrealised_pnl_usd if r.pnl else None)
        eq = _fmt_pnl(equity)
        out.append(
            f"| {r.date} | {r.universe} | `{r.strategy_id}` "
            f"| {r.n_orders} | {r.n_buys_proposed} | {r.n_buys_vetoed} "
            f"| {_fmt_rate(r.veto_rate)} | {rp} | {up} | {eq} |"
        )
    return "\n".join(out)


def render_strategy_summary(rows: list[FusedDayRow]) -> str:
    """Per-strategy running totals across all days observed."""
    if not rows:
        return ""

    by_strategy: dict[str, list[FusedDayRow]] = {}
    for r in rows:
        by_strategy.setdefault(r.strategy_id, []).append(r)

    out: list[str] = []
    out.append("| Strategy | Days | Total orders | Buys proposed | Buys reviewed | Vetoed | Aggregate veto rate | Committee cost |")
    out.append("|----------|-----:|-------------:|--------------:|--------------:|-------:|--------------------:|---------------:|")
    for sid, items in sorted(by_strategy.items()):
        days = len(items)
        n_orders = sum(r.n_orders for r in items)
        n_proposed = sum(r.n_buys_proposed for r in items)
        n_reviewed = sum(r.n_buys_reviewed for r in items)
        n_vetoed = sum(r.n_buys_vetoed for r in items)
        rate = (n_vetoed / n_reviewed) if n_reviewed else None
        cost = sum(r.committee_cost_usd for r in items)
        out.append(
            f"| `{sid}` | {days} | {n_orders} | {n_proposed} | {n_reviewed} | {n_vetoed} "
            f"| {_fmt_rate(rate)} | ${cost:.4f} |"
        )
    return "\n".join(out)


def render_full_report(rows: list[FusedDayRow]) -> str:
    """Combined per-day + per-strategy report."""
    parts: list[str] = []
    parts.append("# DipDiver Scoreboard")
    parts.append("")
    parts.append("> Append-only forward-eval log. One row per (date, universe, strategy).")
    parts.append("> Read [`docs/VALIDATION.md`](../docs/VALIDATION.md) for what these numbers mean and don't mean.")
    parts.append("")
    parts.append("## Per-strategy running totals")
    parts.append("")
    parts.append(render_strategy_summary(rows))
    parts.append("")
    parts.append("## Per-day log (most recent first)")
    parts.append("")
    parts.append(render_markdown_table(rows))
    parts.append("")
    return "\n".join(parts)
