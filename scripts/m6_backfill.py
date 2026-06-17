"""Backfill the scoreboard from existing logs/m3_live/ run records.

Reads every per-day JSON under logs/m3_live/<universe>/*.json and emits one
DaySubmittedEvent per real run (dry-runs are skipped). Idempotent: re-running
skips rows that are already in the scoreboard.

Usage:
    python scripts/m6_backfill.py [--scoreboard path] [--dry-run] [--include-dryrun]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from dipdiver._paths import ui_data_root, ui_logs_dir, ui_scoreboard_path
from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    OrderSummary,
    already_recorded,
    append_event,
    read_events,
    utc_now_iso,
)


log = logging.getLogger(__name__)


def _strategy_id(universe: str, m1_config_stem: str, committee_active: bool) -> str:
    """Stable strategy identifier. With-committee and without are different
    strategies for the purpose of A/B comparison on the scoreboard.
    """
    base = f"{universe}_{m1_config_stem}".replace("_lightgbm", "_lightgbm")
    return f"{base}_committee" if committee_active else base


def _convert_orders(raw_orders: list[dict]) -> list[OrderSummary]:
    """Translate m3_live order dicts to OrderSummary records."""
    out: list[OrderSummary] = []
    for o in raw_orders:
        out.append(
            OrderSummary(
                symbol=o["symbol"],
                side=o["side"],
                notional_usd=o.get("notional"),
                qty=o.get("qty"),
                order_id=o["id"],
                status=o.get("status"),
                submitted_at_utc=o.get("submitted_at", ""),
            )
        )
    return out


def _convert_committee(raw_decisions: list[dict]) -> list[CommitteeVerdictSummary]:
    """Translate m3_live committee_decisions to lean summary form. Per-persona
    verdicts stay in the m3_live JSON (referenced via source_run_record) — we
    don't duplicate them on the scoreboard to keep rows compact.
    """
    out: list[CommitteeVerdictSummary] = []
    for d in raw_decisions:
        out.append(
            CommitteeVerdictSummary(
                symbol=d["symbol"],
                direction=d["direction"],
                approved=d["approved"],
                n_approve=d["n_approve"],
                n_veto=d["n_veto"],
                n_annotate=d["n_annotate"],
                summary_rationale=d.get("summary", ""),
                cost_usd=d.get("cost_usd", 0.0),
            )
        )
    return out


def _build_event_from_record(
    record_path: Path,
    universe: str,
) -> DaySubmittedEvent | None:
    """Convert one m3_live run record to a DaySubmittedEvent.

    The m3_live filename is the trading date (e.g. 2026-06-04.json or
    2026-06-03_dryrun.json). We derive the date from the filename, not from
    the record body, so the scoreboard date is stable across re-runs.
    """
    rec = json.loads(record_path.read_text(encoding="utf-8"))

    # Derive the trading date from the filename (strip _dryrun suffix)
    stem = record_path.stem
    date = stem.replace("_dryrun", "")
    if not (len(date) == 10 and date[4] == "-" and date[7] == "-"):
        log.warning("cannot parse trading date from %s; skipping", record_path)
        return None

    committee_active = bool(rec.get("committee_decisions"))
    # m1 config stem isn't in older records; infer from the universe dir
    # NOTE: This is best-effort. Newer records (post-M6.4) will include
    # config_name directly and we'll prefer it.
    config_name = rec.get("config_name")
    m1_stem = "lightgbm"  # default; corrected below if config_name present
    if config_name:
        # e.g. "dow30_lightgbm.yaml" -> the model part after the universe
        base = config_name.replace(".yaml", "")
        if base.startswith(f"{universe}_"):
            m1_stem = base[len(universe) + 1:]

    strategy_id = _strategy_id(universe, m1_stem, committee_active)
    account = rec.get("account") or {}

    return DaySubmittedEvent(
        date=date,
        universe=universe,
        strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        config_hash=rec.get("config_hash"),
        config_name=config_name,
        signal_date_used=rec.get("signal_date_used"),
        target_holdings=sorted(rec.get("target_post", [])),
        current_holdings_pre=sorted(rec.get("current_holdings_pre", [])),
        adds=sorted(rec.get("adds", [])),
        removes=sorted(rec.get("removes", [])),
        committee_active=committee_active,
        committee_verdicts=_convert_committee(rec.get("committee_decisions", [])),
        orders_submitted=_convert_orders(rec.get("orders", [])),
        account_equity_pre=account.get("equity"),
        account_buying_power_pre=account.get("buying_power"),
        market_open_at_submit=rec.get("market_open"),  # may be None on older records
        dry_run=bool(rec.get("dry_run", False)),
        source_run_record=str(record_path.relative_to(ui_data_root())).replace("\\", "/"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scoreboard", type=Path, default=None,
                        help="Path to scoreboard.jsonl (default: scoreboard/scoreboard.jsonl "
                             "under the UI data root)")
    parser.add_argument("--m3-live-root", type=Path, default=None,
                        help="Default: logs/m3_live/ under the UI data root")
    parser.add_argument("--include-dryrun", action="store_true",
                        help="Include dry-run records (default: skip them)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written; don't append")
    parser.add_argument("--rebuild", action="store_true",
                        help="QW8: backup scoreboard.jsonl to .bak then rebuild from m3_live records "
                             "(use after schema migrations). DaySubmittedEvents only — "
                             "pnl_settled and veto_outcome rows are LOST.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Both default to the UI data root (ui_scoreboard_path / ui_logs_dir), NOT
    # repo_root(): on the VM repo_root() is /app (ephemeral, unmounted), so the
    # old default wrote the scoreboard to /app/scoreboard and m3_live records to
    # /app/logs — invisible to the /scoreboard + /logs pages, which read the UI
    # data root, and lost on container rebuild. read_events()/append_event()
    # already default to ui_scoreboard_path(); this matches them.
    scoreboard_path = args.scoreboard or ui_scoreboard_path()
    m3_live_root = args.m3_live_root or (ui_logs_dir() / "m3_live")

    if not m3_live_root.exists():
        print(f"[m6-backfill] no m3_live records at {m3_live_root}; nothing to do")
        return 0

    # QW8: --rebuild backs up the existing JSONL to .bak.<utc> then truncates so
    # the backfill repopulates from m3_live. PnL/veto rows would need to be
    # re-derived via pnl_settle/veto_backfill afterwards (this tool only knows
    # how to recreate DaySubmittedEvents).
    if args.rebuild and scoreboard_path.exists():
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = scoreboard_path.with_suffix(f".jsonl.bak.{stamp}")
        scoreboard_path.rename(backup)
        print(f"[m6-backfill] --rebuild: backed up to {backup.name}")

    existing = read_events(scoreboard_path)
    print(f"[m6-backfill] scoreboard:    {scoreboard_path}")
    print(f"[m6-backfill] m3_live root:  {m3_live_root}")
    print(f"[m6-backfill] existing rows: {len(existing)}")
    if args.dry_run:
        print(f"[m6-backfill] DRY RUN: no writes will happen")

    n_new = n_skipped = n_dryrun_skipped = 0
    for universe_dir in sorted(m3_live_root.iterdir()):
        if not universe_dir.is_dir():
            continue
        universe = universe_dir.name
        for record in sorted(universe_dir.glob("*.json")):
            # Dry-runs have orders without broker IDs — bail early before
            # we try to build an event from incomplete data.
            try:
                raw = json.loads(record.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                log.warning("cannot parse %s: %s", record, e)
                continue
            if bool(raw.get("dry_run", False)) and not args.include_dryrun:
                n_dryrun_skipped += 1
                log.debug("skip dry-run: %s", record.name)
                continue
            event = _build_event_from_record(record, universe)
            if event is None:
                continue
            if already_recorded(
                existing,
                date=event.date,
                universe=event.universe,
                strategy_id=event.strategy_id,
                event_type="day_submitted",
            ):
                n_skipped += 1
                log.debug("already recorded: %s/%s/%s",
                          event.date, event.universe, event.strategy_id)
                continue
            if not args.dry_run:
                append_event(event, scoreboard_path)
            else:
                print(f"[m6-backfill] WOULD WRITE: "
                      f"{event.date} {event.universe}/{event.strategy_id} "
                      f"({len(event.orders_submitted)} orders, "
                      f"committee={event.committee_active})")
            n_new += 1

    print()
    print(f"[m6-backfill] new rows:        {n_new}")
    print(f"[m6-backfill] already present: {n_skipped}")
    print(f"[m6-backfill] dry-runs skipped: {n_dryrun_skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
