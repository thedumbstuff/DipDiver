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
from dipdiver.brain.baselines.universes import get_universe


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
    with_committee: bool = False,
    config_name: str | None = None,
) -> int:
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient
    from dipdiver.adapters.alpaca.gate import SUPPORTED_UNIVERSES

    # Stage 4 / M11 — Alpaca cannot trade world_indices/crypto/nifty50 even on
    # paper (data API is US-equity-only). Fail clearly instead of silently
    # rejecting orders later.
    if m1.universe not in SUPPORTED_UNIVERSES:
        log.error(
            "universe %r is research-only on the Alpaca adapter. "
            "Export signals via scripts/m3_export_signals.py for external execution.",
            m1.universe,
        )
        return 3

    # QW5: hard-fail at startup when committee is requested but the LLM key is
    # missing. Without this guard, the committee silently fail-opens — every
    # buy gets approved and the user thinks the risk gate is protecting them
    # when in fact it isn't running at all.
    if with_committee:
        from dipdiver.brain.m5.committee import CommitteeConfig
        _cfg = CommitteeConfig()
        if not os.environ.get(_cfg.api_key_env):
            log.error(
                "--with-committee requested but %s is not set. Refusing to "
                "proceed silently — the committee would fail-open and approve "
                "every buy. Set the key in .env.m2 or drop --with-committee.",
                _cfg.api_key_env,
            )
            return 2

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

    # M5 committee: run each proposed BUY through a multi-persona veto panel.
    # Sells go through unchanged — committee can't block risk-reducing exits.
    # That keeps the topk invariant (vetoing a sell + approving a buy would
    # push us over portfolio size) and matches the "downstream of brain" rule.
    committee_decisions: list[dict] = []
    if with_committee and adds:
        adds, committee_decisions = _run_committee(
            m1, adds=adds, scored=scored, signal_date=signal_date,
            current_holdings=current_holdings, equity=account.equity, topk=topk,
        )
        log.info("after committee: adds=%s  removes=%s",
                 sorted(adds), sorted(removes))

    if not adds and not removes:
        log.info("portfolio already at target — nothing to do")
        run_record = _build_record(
            signal_date, account, current_holdings, target, adds, removes,
            orders=[], dry_run=dry_run, committee_decisions=committee_decisions,
            m1=m1, config_name=config_name, market_open=market_open,
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
        orders=orders, dry_run=dry_run, committee_decisions=committee_decisions,
        m1=m1, config_name=config_name, market_open=market_open,
    )
    _write_record(output_dir, run_record)
    return 0


def _run_committee(
    m1: BaselineConfig,
    adds: set,
    scored: list[tuple[str, float]],
    signal_date: str,
    current_holdings: set,
    equity: float,
    topk: int,
) -> tuple[set, list[dict]]:
    """Submit each proposed buy to the M5 committee. Return (approved_adds, decisions)."""
    from dipdiver.brain.m5 import CommitteeConfig, TradeProposal, review

    universe = get_universe(m1.universe)
    universe_desc = (
        f"{len(universe.instruments)} {m1.region.upper()} instruments; "
        f"top-{topk} / drop-{m1.backtest_params.get('n_drop', 3)} daily rebalance"
    )
    score_lookup = dict(scored)
    target_notional = equity / topk
    cfg = CommitteeConfig()  # DeepSeek-chat default; override via env if needed

    approved: set = set()
    decisions: list[dict] = []
    for symbol in sorted(adds):
        proposal = TradeProposal(
            symbol=symbol, direction="buy",
            universe=m1.universe, benchmark=m1.benchmark,
            universe_description=universe_desc,
            signal_score=score_lookup.get(symbol, 0.0),
            signal_date=signal_date,
            notional_usd=target_notional,
            current_holdings=sorted(current_holdings),
            test_window=f"{m1.test_start} -> {m1.test_end}",
        )
        decision = review(proposal, cfg=cfg)
        decisions.append({
            "symbol": symbol,
            "direction": "buy",
            "approved": decision.approved,
            "n_approve": decision.n_approve,
            "n_veto": decision.n_veto,
            "n_annotate": decision.n_annotate,
            "summary": decision.majority_rationale,
            "verdicts": [v.model_dump() for v in decision.verdicts],
            "cost_usd": decision.cost_usd,
        })
        outcome = "APPROVED" if decision.approved else "VETOED"
        log.info(f"committee {outcome} buy {symbol}: {decision.majority_rationale}")
        if decision.approved:
            approved.add(symbol)
    log.info("committee: %d/%d buys approved", len(approved), len(adds))
    return approved, decisions


def _build_record(
    signal_date: str,
    account,
    current_holdings: set,
    target: set,
    adds: set,
    removes: set,
    orders: list[dict],
    dry_run: bool,
    committee_decisions: list[dict] | None = None,
    m1: BaselineConfig | None = None,
    config_name: str | None = None,
    market_open: bool | None = None,
) -> dict:
    return {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "dry_run": dry_run,
        "signal_date_used": signal_date,
        "config_name": config_name,
        "config_hash": m1.config_hash if m1 is not None else None,
        "universe": m1.universe if m1 is not None else None,
        "market_open": market_open,
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
        "committee_decisions": committee_decisions or [],
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
    parser.add_argument("--with-committee", action="store_true",
                        help="Run each proposed buy through the M5 risk-veto committee. "
                             "Vetoed buys are logged but not submitted. Sells pass through unchanged.")
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
    print(f"[m3-live] committee: {'ON' if args.with_committee else 'off'}")
    print(f"[m3-live] output:    {output_dir}")
    print()

    try:
        return run_once(
            m1=m1, signals_csv=args.signals, output_dir=output_dir,
            dry_run=args.dry_run, force_off_hours=args.force,
            with_committee=args.with_committee,
            config_name=args.m1_config,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("m3-live failed")
        print(f"[m3-live] ERROR: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
