"""Dry-run the M5 committee against a day's worth of M1/M2 signals.

For each rotation the strategy would make today, ask the committee. Print
the verdicts. No Alpaca, no real orders — this is the safe way to see what
the committee thinks before wiring it into the live runner.

Usage:
    python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml

    # Pin a specific signal date (default: latest in csv)
    python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml --signal-date 2024-01-15

    # Use OpenAI instead of DeepSeek (set OPENAI_API_KEY)
    python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml --provider openai
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import sys
from pathlib import Path

from dipdiver._paths import repo_root
from dipdiver.adapters.alpaca import compute_target_holdings
from dipdiver.brain.baselines.config import BaselineConfig, load_config
from dipdiver.brain.baselines.universes import get_universe
from dipdiver.brain.m5 import CommitteeConfig, TradeProposal, review


log = logging.getLogger(__name__)


PROVIDER_CONFIGS = {
    "deepseek": CommitteeConfig(
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        pricing_tier="deepseek",
    ),
    "openai": CommitteeConfig(
        model="gpt-4o-mini",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        pricing_tier="openai_gpt4o",
    ),
}


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


def _load_signals_for_date(csv_path: Path, signal_date: str | None) -> tuple[str, list[tuple[str, float]]]:
    """Return (date_used, [(symbol, score), ...]).

    With signal_date=None we use the latest date in the file (mirrors m3_live_alpaca).
    """
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
    if signal_date is None:
        signal_date = max(by_date.keys())
    if signal_date not in by_date:
        raise RuntimeError(f"signal date {signal_date} not in CSV (have {min(by_date)}..{max(by_date)})")
    return signal_date, by_date[signal_date]


def _universe_description(m1: BaselineConfig) -> str:
    """One-line context string sent to the committee."""
    universe = get_universe(m1.universe)
    n = len(universe.instruments)
    return f"{n} {m1.region.upper()} instruments; daily-rebalance top-{m1.backtest_params.get('topk', 10)} / drop-{m1.backtest_params.get('n_drop', 3)} strategy"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-config", required=True)
    parser.add_argument("--signals", type=Path, default=None,
                        help="Default: data/signals/<m1-config-stem>.csv")
    parser.add_argument("--signal-date", default=None,
                        help="Pin a specific signal date in the CSV. Default: latest.")
    parser.add_argument("--current-holdings", default=None,
                        help="Comma-separated symbols representing the current portfolio. "
                             "Default: empty (cold start, all top-K are adds).")
    parser.add_argument("--provider", default="deepseek",
                        choices=sorted(PROVIDER_CONFIGS.keys()))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    n_loaded = _load_env_file(repo_root() / ".env.m2")
    if n_loaded:
        print(f"[m5-review] loaded {n_loaded} variable(s) from .env.m2")

    cfg_dir = repo_root() / "dipdiver" / "brain" / "baselines" / "configs"
    m1 = load_config(cfg_dir / args.m1_config)
    topk = int(m1.backtest_params.get("topk", 10))
    n_drop = int(m1.backtest_params.get("n_drop", 3))

    signals_csv = args.signals if args.signals else (
        repo_root() / "data" / "signals" / f"{args.m1_config.replace('.yaml', '')}.csv"
    )
    if not signals_csv.exists():
        print(f"[m5-review] ERROR: signals CSV not found at {signals_csv}")
        print("[m5-review] Generate it: python scripts/m3_export_signals.py "
              f"--m1-config {args.m1_config}")
        return 1

    signal_date, scored = _load_signals_for_date(signals_csv, args.signal_date)
    current_holdings = set(
        s.strip().upper() for s in (args.current_holdings or "").split(",") if s.strip()
    )

    target, adds, removes = compute_target_holdings(
        scored=scored, current_holdings=current_holdings, topk=topk, n_drop=n_drop,
    )

    print(f"[m5-review] M1:               {args.m1_config}")
    print(f"[m5-review] signal date:      {signal_date}")
    print(f"[m5-review] provider:         {args.provider}")
    print(f"[m5-review] topk / n_drop:    {topk} / {n_drop}")
    print(f"[m5-review] starting holdings: {sorted(current_holdings) or '(empty)'}")
    print(f"[m5-review] target:            {sorted(target)}")
    print(f"[m5-review] adds:              {sorted(adds)}")
    print(f"[m5-review] removes:           {sorted(removes)}")
    print()

    if not adds and not removes:
        print("[m5-review] no rotations today — nothing to review")
        return 0

    cfg = PROVIDER_CONFIGS[args.provider]
    score_lookup = dict(scored)
    universe_desc = _universe_description(m1)
    notional = 10_000.0  # placeholder; live runner uses equity / topk

    n_total = n_approved = n_blocked = 0
    cost_total = 0.0
    in_total = out_total = 0

    for symbol in sorted(adds) + sorted(removes):
        direction = "buy" if symbol in adds else "sell"
        proposal = TradeProposal(
            symbol=symbol,
            direction=direction,
            universe=m1.universe,
            benchmark=m1.benchmark,
            universe_description=universe_desc,
            signal_score=score_lookup.get(symbol, 0.0),
            signal_date=signal_date,
            notional_usd=notional,
            current_holdings=sorted(current_holdings),
            test_window=f"{m1.test_start} -> {m1.test_end}",
        )
        print(f"\n--- {direction.upper()} {symbol}  (score {proposal.signal_score:+.4f}) ---")
        decision = review(proposal, cfg=cfg)
        cost_total += decision.cost_usd
        in_total += decision.in_tokens
        out_total += decision.out_tokens
        n_total += 1
        if decision.approved:
            n_approved += 1
        else:
            n_blocked += 1
        outcome = "APPROVED" if decision.approved else "VETOED"
        print(f"    [{outcome}] approves={decision.n_approve} vetos={decision.n_veto} "
              f"annotations={decision.n_annotate}")
        for v in decision.verdicts:
            tag = {"approve": "+", "veto": "-", "annotate": "~"}[v.decision]
            print(f"    {tag} {v.persona:<13} conf={v.confidence:.2f}: {v.rationale[:200]}")

    print()
    print("=" * 80)
    print(f"SUMMARY  {n_total} trades reviewed: {n_approved} approved, {n_blocked} blocked")
    print(f"  veto rate:  {n_blocked / n_total:.1%}")
    print(f"  cost:       ${cost_total:.4f}  ({in_total:,} in + {out_total:,} out)")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
