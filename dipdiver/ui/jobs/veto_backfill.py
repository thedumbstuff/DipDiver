"""veto_backfill — write VetoOutcomeEvent rows for past committee vetoes.

For each DaySubmittedEvent with `committee_active=True`, for each verdict
where `approved=False`, look up what that symbol actually did between
`entry_date` and `entry_date + holding_window_days`, and append a
VetoOutcomeEvent. Per-symbol idempotent.
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timezone

from dipdiver._paths import ui_scoreboard_path
from dipdiver.harness.scoreboard import (
    VetoOutcomeEvent,
    already_recorded,
    append_event,
    read_events,
    utc_now_iso,
)
from dipdiver.harness.veto_regret import compute_counterfactual, default_price_provider

log = logging.getLogger(__name__)


# Default holding window — overridable per StrategyConfig in M14.
DEFAULT_HOLDING_WINDOW_DAYS = 5


def _today_utc() -> date_cls:
    return datetime.now(timezone.utc).date()


def run() -> dict:
    sb_path = ui_scoreboard_path()
    events = read_events(sb_path)
    today = _today_utc()

    # Collect (event, verdict) pairs that need an outcome row.
    candidates: list[tuple[str, str, str, str, date_cls]] = []  # (date, universe, sid, symbol, entry)
    for e in events:
        if e.event_type != "day_submitted":
            continue
        if not getattr(e, "committee_active", False):
            continue
        try:
            entry_date = date_cls.fromisoformat(e.date)
        except ValueError:
            continue
        for v in e.committee_verdicts:
            if v.approved or v.direction != "buy":
                continue
            settle_target = entry_date.toordinal() + DEFAULT_HOLDING_WINDOW_DAYS
            if date_cls.fromordinal(settle_target) > today:
                continue
            candidates.append((e.date, e.universe, e.strategy_id, v.symbol, entry_date))

    if not candidates:
        return {
            "rc": 0,
            "candidates": 0,
            "events_written": 0,
            "message": "no veto candidates with materialised settle date.",
        }

    try:
        provider = default_price_provider()
    except Exception as e:  # noqa: BLE001
        log.exception("veto_backfill: provider init failed")
        return {
            "rc": 1,
            "candidates": len(candidates),
            "events_written": 0,
            "error": f"provider init failed: {type(e).__name__}: {e}",
        }

    written = 0
    skipped = 0
    no_data = 0
    for date_s, universe, strategy_id, symbol, entry_date in candidates:
        if already_recorded(
            events,
            date=date_s,
            universe=universe,
            strategy_id=strategy_id,
            event_type="veto_outcome",
            symbol=symbol,
        ):
            skipped += 1
            continue
        result = compute_counterfactual(
            symbol=symbol,
            entry_date=entry_date,
            holding_window_days=DEFAULT_HOLDING_WINDOW_DAYS,
            provider=provider,
            today_utc=today,
        )
        if result is None:
            no_data += 1
            continue
        event = VetoOutcomeEvent(
            date=date_s,
            universe=universe,
            strategy_id=strategy_id,
            timestamp_utc=utc_now_iso(),
            settle_date=result.settle_date,
            symbol=symbol,
            estimated_entry_price=result.estimated_entry_price,
            actual_price_at_settle=result.actual_price_at_settle,
            counterfactual_pnl_pct=result.counterfactual_pnl_pct,
            holding_window_days=result.holding_window_days,
        )
        append_event(event, sb_path)
        written += 1

    return {
        "rc": 0,
        "candidates": len(candidates),
        "events_written": written,
        "already_present": skipped,
        "no_price_data": no_data,
        "message": f"{written} written, {skipped} skipped (already present), {no_data} no data",
    }
