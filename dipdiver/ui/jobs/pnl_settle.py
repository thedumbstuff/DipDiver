"""pnl_settle — fetch settled P&L for unsettled day_submitted rows.

For each `DaySubmittedEvent` whose date is strictly before today UTC and has
no matching `pnl_settled` row yet, fetch the account-wide daily P&L from the
broker, attribute proportionally to strategies that submitted on that date,
and append one `PnlSettledEvent` per strategy.

Idempotent: re-running adds zero duplicates (guarded via `already_recorded`).
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timezone
from typing import Callable

from dipdiver._paths import ui_scoreboard_path
from dipdiver.harness.scoreboard import (
    DaySubmittedEvent,
    PnlSettledEvent,
    ScoreboardEvent,
    already_recorded,
    append_event,
    read_events,
    utc_now_iso,
)

log = logging.getLogger(__name__)


# Tests inject a fake provider via this hook. None → use Alpaca.
_PROVIDER_OVERRIDE: Callable[[date_cls], "DailyPnlSnapshot"] | None = None  # type: ignore[name-defined]


def set_provider_override(fn: Callable[[date_cls], "DailyPnlSnapshot"] | None) -> None:  # type: ignore[name-defined]
    global _PROVIDER_OVERRIDE
    _PROVIDER_OVERRIDE = fn


def _resolve_provider():
    if _PROVIDER_OVERRIDE is not None:
        return _PROVIDER_OVERRIDE
    from dipdiver.adapters.alpaca.portfolio import _make_alpaca_provider
    return _make_alpaca_provider()


def _today_utc() -> date_cls:
    return datetime.now(timezone.utc).date()


def _unsettled_dates(events: list[ScoreboardEvent], today: date_cls) -> list[str]:
    """Distinct day_submitted dates strictly before today with no pnl_settled
    row covering any of their strategies."""
    have_pnl: set[tuple[str, str, str]] = set()
    submitted_dates: set[str] = set()
    for e in events:
        if e.event_type == "pnl_settled":
            have_pnl.add((e.date, e.universe, e.strategy_id))
        elif e.event_type == "day_submitted":
            try:
                d = date_cls.fromisoformat(e.date)
            except ValueError:
                continue
            if d < today:
                submitted_dates.add(e.date)
    # Only return dates where at least one (universe, strategy_id) lacks pnl_settled.
    out: list[str] = []
    for d in sorted(submitted_dates):
        strategies = [
            (e.universe, e.strategy_id) for e in events
            if e.event_type == "day_submitted" and e.date == d
        ]
        if any((d, u, s) not in have_pnl for u, s in strategies):
            out.append(d)
    return out


def run() -> dict:
    """Settle P&L for any past-day submitted rows that don't have it yet."""
    from dipdiver.adapters.alpaca.portfolio import attribute_strategies

    sb_path = ui_scoreboard_path()
    events = read_events(sb_path)
    today = _today_utc()
    unsettled = _unsettled_dates(events, today)

    if not unsettled:
        return {
            "rc": 0,
            "dates_processed": 0,
            "events_written": 0,
            "message": "no unsettled day_submitted rows.",
        }

    try:
        provider = _resolve_provider()
    except Exception as e:  # noqa: BLE001
        log.exception("pnl_settle: could not construct provider")
        return {
            "rc": 1,
            "dates_processed": 0,
            "events_written": 0,
            "error": f"provider init failed: {type(e).__name__}: {e}",
        }

    written = 0
    errors: list[str] = []
    for d in unsettled:
        try:
            target = date_cls.fromisoformat(d)
            snapshot = provider(target)
        except Exception as e:  # noqa: BLE001
            log.warning("pnl_settle: provider failed for %s: %s", d, e)
            errors.append(f"{d}: {type(e).__name__}: {e}")
            continue
        submitted_on_d: list[DaySubmittedEvent] = [
            e for e in events
            if e.event_type == "day_submitted" and e.date == d
        ]
        shares = attribute_strategies(submitted_on_d)
        for share in shares:
            if already_recorded(
                events,
                date=d,
                universe=share.universe,
                strategy_id=share.strategy_id,
                event_type="pnl_settled",
            ):
                continue
            event = PnlSettledEvent(
                date=d,
                universe=share.universe,
                strategy_id=share.strategy_id,
                timestamp_utc=utc_now_iso(),
                realised_pnl_usd=round(snapshot.realised_pnl_usd * share.weight, 2),
                unrealised_pnl_usd=round(snapshot.unrealised_pnl_usd * share.weight, 2),
                holdings_at_close=snapshot.holdings_at_close,
                equity_at_close=round(snapshot.equity_at_close * share.weight, 2),
                source="alpaca_portfolio_history",
                attribution_method=share.attribution_method,  # type: ignore[arg-type]
                attribution_weight=round(share.weight, 6),
                slippage_usd=snapshot.slippage_usd,
                commission_usd=snapshot.commission_usd,
            )
            append_event(event, sb_path)
            written += 1

    result: dict = {
        "rc": 0 if not errors else 1,
        "dates_processed": len(unsettled),
        "events_written": written,
    }
    if errors:
        result["errors"] = errors
        result["message"] = f"{written} events written, {len(errors)} dates failed"
    else:
        result["message"] = f"{written} events written for {len(unsettled)} dates"
    return result
