"""Append-only event log for the M6 forward-eval scoreboard.

The scoreboard is the project's tier-1+ evidence. Per VALIDATION.md, it MUST be
append-only — edits or deletions break the audit chain. Every event is one
JSONL line; later facts about the same trading day are written as separate
events (e.g. P&L settled after T+1), then fused at render time.

Schema overview:

  DaySubmittedEvent  — orders submitted on day D (proposal + committee + orders)
  PnlSettledEvent    — realised + unrealised P&L for day D (written T+1 or later)
  VetoOutcomeEvent   — what a vetoed trade would have done by T+N (written T+N)

Reader: read_events() returns the polymorphic list (Pydantic discriminator on
event_type). Use fuse_by_day() in render.py to collapse events into a single
per-day row for human display.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter


# ---------------------------------------------------------------------------
# Sub-records
# ---------------------------------------------------------------------------


class OrderSummary(BaseModel):
    """One order as submitted to the broker. Fills are tracked separately."""

    symbol: str
    side: Literal["buy", "sell"]
    notional_usd: float | None = None  # close orders have no notional
    qty: float | None = None
    order_id: str
    status: str | None = None  # broker-side status at submit time
    submitted_at_utc: str  # ISO timestamp


class CommitteeVerdictSummary(BaseModel):
    """Per-symbol committee outcome. Persona-level rationales live in the
    source m3_live run record, not duplicated here (keep scoreboard rows lean).
    """

    symbol: str
    direction: Literal["buy", "sell"]
    approved: bool
    n_approve: int
    n_veto: int
    n_annotate: int
    summary_rationale: str  # the deciding voice's text (truncated)
    cost_usd: float


# ---------------------------------------------------------------------------
# Events (one per JSONL line)
# ---------------------------------------------------------------------------


class _EventBase(BaseModel):
    date: str  # YYYY-MM-DD; the trading day this event is *about*
    universe: str
    strategy_id: str  # e.g. "dow30_lightgbm" or "dow30_lightgbm_committee"
    timestamp_utc: str  # when this row was *written* (not the trade day)


class DaySubmittedEvent(_EventBase):
    """Day-D proposal + orders submitted. Written when m3_live_alpaca finishes."""

    event_type: Literal["day_submitted"] = "day_submitted"
    config_hash: str | None = None  # M1 config hash; None if unknown (e.g. backfill)
    config_name: str | None = None  # e.g. "dow30_lightgbm.yaml"
    signal_date_used: str | None = None  # which signal row drove the rotation
    target_holdings: list[str] = Field(default_factory=list)
    current_holdings_pre: list[str] = Field(default_factory=list)
    adds: list[str] = Field(default_factory=list)
    removes: list[str] = Field(default_factory=list)
    committee_active: bool = False
    committee_verdicts: list[CommitteeVerdictSummary] = Field(default_factory=list)
    orders_submitted: list[OrderSummary] = Field(default_factory=list)
    account_equity_pre: float | None = None
    account_buying_power_pre: float | None = None
    market_open_at_submit: bool | None = None
    dry_run: bool = False
    source_run_record: str | None = None  # path to the m3_live JSON


class PnlSettledEvent(_EventBase):
    """Realised + unrealised P&L for day D. Written T+1 once close prices land.

    Written as a NEW event, not a mutation — preserves the audit chain.

    Attribution: when a single Alpaca account hosts multiple strategies, daily
    P&L is allocated proportionally by submitted-notional. `attribution_method`
    documents which rule was used; `attribution_weight` is the fraction
    (1.0 == sole strategy on that day).
    """

    event_type: Literal["pnl_settled"] = "pnl_settled"
    realised_pnl_usd: float
    unrealised_pnl_usd: float
    holdings_at_close: dict[str, float] = Field(default_factory=dict)  # symbol -> market value
    equity_at_close: float
    source: str = "alpaca_portfolio_history"  # where the P&L came from
    attribution_method: Literal["single_strategy", "weighted_by_notional"] = "single_strategy"
    attribution_weight: float = 1.0
    slippage_usd: float | None = None
    commission_usd: float | None = None


class VetoOutcomeEvent(_EventBase):
    """What a vetoed buy would have done by settle_date. Written by the
    veto-regret backfill, typically T+5 or T+10 after the veto.
    """

    event_type: Literal["veto_outcome"] = "veto_outcome"
    settle_date: str  # YYYY-MM-DD when this outcome is measured
    symbol: str
    estimated_entry_price: float | None = None  # what we'd have paid
    actual_price_at_settle: float
    counterfactual_pnl_pct: float  # if positive, veto cost us money
    holding_window_days: int


ScoreboardEvent = Annotated[
    Union[DaySubmittedEvent, PnlSettledEvent, VetoOutcomeEvent],
    Field(discriminator="event_type"),
]

_EVENT_ADAPTER: TypeAdapter[ScoreboardEvent] = TypeAdapter(ScoreboardEvent)


# ---------------------------------------------------------------------------
# Append + read helpers
# ---------------------------------------------------------------------------


DEFAULT_SCOREBOARD_PATH = Path("scoreboard") / "scoreboard.jsonl"


def utc_now_iso() -> str:
    """Single source of truth for event timestamps. Always UTC, ISO format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append_event(event: ScoreboardEvent, path: Path = DEFAULT_SCOREBOARD_PATH) -> None:
    """Append one event as a single JSONL line.

    Append-only: never use this to overwrite an existing line. If you need to
    correct a row, write a new event (a future SchemaVersionEvent could mark
    superseded rows — not implemented yet).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = event.model_dump_json()
    # Open in append-binary mode so we can guarantee a clean newline boundary
    # even if a prior writer crashed mid-line.
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
        f.write(b"\n")
        f.flush()
        os.fsync(f.fileno())


def read_events(path: Path = DEFAULT_SCOREBOARD_PATH) -> list[ScoreboardEvent]:
    """Read all events from the scoreboard JSONL. Skips blank lines."""
    if not path.exists():
        return []
    events: list[ScoreboardEvent] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        events.append(_EVENT_ADAPTER.validate_json(raw))
    return events


def filter_events(
    events: list[ScoreboardEvent],
    *,
    universe: str | None = None,
    strategy_id: str | None = None,
    event_type: str | None = None,
    date: str | None = None,
) -> list[ScoreboardEvent]:
    """Lightweight filter for downstream consumers."""
    out = events
    if universe is not None:
        out = [e for e in out if e.universe == universe]
    if strategy_id is not None:
        out = [e for e in out if e.strategy_id == strategy_id]
    if event_type is not None:
        out = [e for e in out if e.event_type == event_type]
    if date is not None:
        out = [e for e in out if e.date == date]
    return out


def already_recorded(
    events: list[ScoreboardEvent],
    *,
    date: str,
    universe: str,
    strategy_id: str,
    event_type: str,
    symbol: str | None = None,
) -> bool:
    """Idempotence check: has this (date, universe, strategy, type[, symbol])
    already been written?

    For `veto_outcome` rows pass a symbol — vetos are per-symbol so one
    DaySubmittedEvent can produce many outcome rows on the same key tuple.
    For `day_submitted` and `pnl_settled` leave symbol=None.
    """
    for e in events:
        if not (
            e.event_type == event_type
            and e.date == date
            and e.universe == universe
            and e.strategy_id == strategy_id
        ):
            continue
        if symbol is not None and isinstance(e, VetoOutcomeEvent):
            if e.symbol != symbol:
                continue
        return True
    return False
