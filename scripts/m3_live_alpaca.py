"""One-shot daily live runner: read latest signals, apply TopkDropoutStrategy,
reconcile Alpaca paper positions to the target portfolio.

Designed to be run once per trading day (manually, via cron, or via Windows
Task Scheduler). Idempotent — if today's target already matches current
positions, places no orders.

Usage:
    # set keys first (auto-loaded from .env.m2 in repo root if present)
    python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml

    # dry-run shows planned orders without submitting
    python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml --dry-run

    # bypass the market-open check (for off-hours testing)
    python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml --force

Each invocation writes one JSON log row to logs/m3_live/<universe>/<date>.json
so subsequent days can be diffed / verified against a parallel backtest.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

from dipdiver._paths import repo_root
from dipdiver.adapters.alpaca import compute_target_holdings
from dipdiver.brain.baselines.config import BaselineConfig, load_config


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# env loading (same pattern as scripts/m2_lite_run.py)
# ---------------------------------------------------------------------------


def _load_env_file(path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


# ---------------------------------------------------------------------------
# signal CSV reading
# ---------------------------------------------------------------------------


def _load_latest_signals(csv_path: Path) -> tuple[str, list[tuple[str, float]]]:
    """Return (signal_date, [(symbol, score), ...]) for the most recent date."""
    from collections import defaultdict

    by_date: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row["date"].strip()
            symbol = row["symbol"].strip().upper()
            try:
                score = float(row["score"])
            except ValueError:
                continue
            if score != score:
                continue
            by_date[date].append((symbol, score))
    if not by_date:
        raise RuntimeError(f"{csv_path} contains no usable rows")
    latest_date = max(by_date.keys())
    return latest_date, by_date[latest_date]


# ---------------------------------------------------------------------------
# main loop
# ---------------------------------------------------------------------------


def run_once(
    m1: BaselineConfig,
    signals_csv: Path,
    output_dir: Path,
    dry_run: bool,
    force_off_hours: bool,
) -> int:
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient

    topk = int(m1.backtest_params.get("topk", 10))
    n_drop = int(m1.backtest_params.get("n_drop", 3))

    # 1. Connect Alpaca + check market status
    client = AlpacaPaperClient()
    account = client.get_account()
    log.info(
        "alpaca: cash=$%.2f equity=$%.2f buying_power=$%.2f status=%s",
        account.cash, account.equity, account.buying_power, account.status,
    )

    market_open = client.market_is_open()
    log.info("market open right now: %s", market_open)
    if not market_open and not force_off_hours:
        log.warning("market is closed; skipping. pass --force to override.")
        return 0

    # 2. Latest signal
    signal_date, scored = _load_latest_signals(signals_csv)
    log.info("using signal date %s (%d symbols scored)", signal_date, len(scored))

    # 3. Current positions
    positions = client.get_positions()
    current_holdings = {p.symbol for p in positions}
    log.info("current holdings (%d): %s", len(current_holdings), sorted(current_holdings))

    # 4. Compute target via shared TopkDropout
    target, adds, removes = compute_target_holdings(
        scored=scored,
        current_holdings=current_holdings,
        topk=topk,
        n_drop=n_drop,
    )
    log.info(
        "target (%d): adds=%s  removes=%s",
        len(target), sorted(adds), sorted(removes),
    )

    if not adds and not removes:
        log.info("portfolio already at target — nothing to do")
        run_record = _build_record(
            signal_date, account, current_holdings, target, adds, removes,
            orders=[], dry_run=dry_run,
        )
        _write_record(output_dir, run_record)
        return 0

    # 5. Place orders
    orders: list[dict] = []
    if dry_run:
        log.info("DRY RUN — would place orders but not submitting")
        for sym in removes:
            orders.append({"symbol": sym, "side": "sell", "dry_run": True})
        target_notional = account.equity / topk
        for sym in adds:
            orders.append({
                "symbol": sym, "side": "buy",
                "notional": round(target_notional, 2),
                "dry_run": True,
            })
    else:
        # Close before opening so the buying power frees up first.
        for sym in removes:
            try:
                o = client.close_position(sym)
                orders.append(o)
                log.info("CLOSED %s: order=%s", sym, o.get("id"))
            except Exception as e:  # noqa: BLE001
                orders.append({"symbol": sym, "side": "sell", "error": str(e)})
                log.error("CLOSE %s failed: %s", sym, e)

        target_notional = account.equity / topk
        for sym in adds:
            try:
                o = client.open_position(sym, notional_usd=target_notional)
                orders.append(o)
                log.info("OPENED %s @ $%.2f notional: order=%s",
                         sym, target_notional, o.get("id"))
            except Exception as e:  # noqa: BLE001
                orders.append({"symbol": sym, "side": "buy", "error": str(e)})
                log.error("OPEN %s failed: %s", sym, e)

    # 6. Persist run record
    run_record = _build_record(
        signal_date, account, current_holdings, target, adds, removes,
        orders=orders, dry_run=dry_run,
    )
    _write_record(output_dir, run_record)
    return 0


def _build_record(
    signal_date: str,
    account,
    current_holdings: set,
    target: set,
    adds: set,
    removes: set,
    orders: list[dict],
    dry_run: bool,
) -> dict:
    return {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "signal_date_used": signal_date,
        "account": {
            "cash": account.cash,
            "equity": account.equity,
            "buying_power": account.buying_power,
            "status": account.status,
        },
        "current_holdings_pre": sorted(current_holdings),
        "target_post": sorted(target),
        "adds": sorted(adds),
        "removes": sorted(removes),
        "orders": orders,
    }


def _write_record(output_dir: Path, record: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    today_iso = dt.datetime.now().strftime("%Y-%m-%d")
    suffix = "_dryrun" if record["dry_run"] else ""
    path = output_dir / f"{today_iso}{suffix}.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True), encoding="utf-8")
    log.info("wrote run record: %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-config", required=True,
                        help="M1 baseline YAML, e.g. dow30_lightgbm.yaml")
    parser.add_argument("--signals", type=Path, default=None,
                        help="Signal CSV path. Default: data/signals/<m1-config-stem>.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan orders but don't submit (no Alpaca write)")
    parser.add_argument("--force", action="store_true",
                        help="Run even when the market is closed")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Auto-load .env.m2 so user doesn't have to source.
    env_path = repo_root() / ".env.m2"
    n_loaded = _load_env_file(env_path)
    if n_loaded:
        print(f"[m3-live] loaded {n_loaded} variable(s) from {env_path.name}")

    cfg_dir = repo_root() / "dipdiver" / "brain" / "baselines" / "configs"
    m1 = load_config(cfg_dir / args.m1_config)

    if args.signals is None:
        stem = args.m1_config.replace(".yaml", "").replace(".yml", "")
        args.signals = repo_root() / "data" / "signals" / f"{stem}.csv"
    if not args.signals.exists():
        print(f"[m3-live] ERROR: signals CSV not found at {args.signals}")
        print(f"[m3-live] generate it first: "
              f"python scripts/m3_export_signals.py --m1-config {args.m1_config}")
        return 1

    output_dir = repo_root() / "logs" / "m3_live" / m1.universe

    print(f"[m3-live] M1:        {args.m1_config}  (universe={m1.universe})")
    print(f"[m3-live] signals:   {args.signals}")
    print(f"[m3-live] mode:      {'DRY-RUN' if args.dry_run else 'LIVE PAPER'}")
    print(f"[m3-live] output:    {output_dir}")
    print()

    try:
        return run_once(
            m1=m1, signals_csv=args.signals, output_dir=output_dir,
            dry_run=args.dry_run, force_off_hours=args.force,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("m3-live failed")
        print(f"[m3-live] ERROR: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
