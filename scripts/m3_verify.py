"""Day-by-day parity check: Lean's actual orders vs Qlib's TopkDropoutStrategy
expected orders derived from the same signal CSV.

What it verifies:
  - Same set of trading days (Lean ran on every day we have signals for, modulo
    market-closed days).
  - On each day where a rotation should happen, Lean rotates in the same set of
    symbols (adds) and out the same set (removes).
  - Quantity-level parity is NOT checked (Lean rounds to whole shares, our
    simulation works in fractional weights; that drift is expected).

Use case:
  python scripts/m3_verify.py --m1-config dow30_lightgbm.yaml
  python scripts/m3_verify.py --m1-config dow30_lightgbm.yaml --lean-run lean_projects/dipdiver_dow30_lightgbm/backtests/2026-06-02_21-31-11
  python scripts/m3_verify.py --m1-config dow30_lightgbm.yaml --max-mismatch-rows 25
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dipdiver._paths import repo_root
from dipdiver.brain.baselines.config import load_config as load_m1_config


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Expected orders: simulate TopkDropoutStrategy from signals.csv
# ---------------------------------------------------------------------------


@dataclass
class DayOrders:
    date: str
    adds: set[str] = field(default_factory=set)
    removes: set[str] = field(default_factory=set)


def simulate_expected_orders(
    signals_csv: Path,
    topk: int,
    n_drop: int,
) -> dict[str, DayOrders]:
    """Replay TopkDropoutStrategy in Python over a signals.csv.

    Output is keyed by the date the rotation happens IN LEAN'S WALL CLOCK —
    i.e., the day we'd see those orders fire in Lean's log. Lean's algorithm
    uses signal dated d-1 to drive trades on day d, so a signal row dated
    2024-01-02 produces orders on 2024-01-03.
    """
    # Load signals grouped by date
    signals_by_date: dict[str, list[tuple[str, float]]] = defaultdict(list)
    with signals_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row["date"].strip()
            symbol = row["symbol"].strip().upper()
            try:
                score = float(row["score"])
            except ValueError:
                continue
            if score != score:  # NaN
                continue
            signals_by_date[date].append((symbol, score))

    signal_dates = sorted(signals_by_date.keys())
    if not signal_dates:
        return {}

    # Walk forward, simulating the strategy.
    expected: dict[str, DayOrders] = {}
    current_holdings: set[str] = set()

    # The signal at signal_dates[i] drives the trade on signal_dates[i+1]
    # (matching Lean's `_previous_signal_date` lookup).
    for i in range(len(signal_dates) - 1):
        signal_date = signal_dates[i]
        order_date = signal_dates[i + 1]
        signals_sorted = sorted(
            signals_by_date[signal_date],
            key=lambda kv: kv[1],
            reverse=True,
        )
        scores = {sym: score for sym, score in signals_sorted}

        if not current_holdings:
            target = {sym for sym, _ in signals_sorted[:topk]}
        else:
            current_ranked = sorted(
                current_holdings,
                key=lambda t: scores.get(t, float("-inf")),
            )
            to_drop = set(current_ranked[:n_drop])
            non_held_top = [
                sym for sym, _ in signals_sorted
                if sym not in current_holdings
            ][:n_drop]
            to_add = set(non_held_top)
            target = (current_holdings - to_drop) | to_add

        adds = target - current_holdings
        removes = current_holdings - target
        if adds or removes:
            expected[order_date] = DayOrders(date=order_date, adds=adds, removes=removes)
        current_holdings = target

    return expected


# ---------------------------------------------------------------------------
# Actual orders: parse Lean's order-events.json
# ---------------------------------------------------------------------------


def parse_lean_orders(events_json: Path) -> dict[str, DayOrders]:
    """Extract the {adds, removes} per trading day from Lean's order log.

    We only count FILLED events. `direction == "buy"` and a previously-zero
    position becoming positive is an add; `direction == "sell"` and a previously
    positive position becoming zero is a remove. Same logic as the Lean
    algorithm's _rebalance: we open only on add, close only on remove.
    """
    with events_json.open("r", encoding="utf-8") as f:
        events = json.load(f)

    by_date: dict[str, DayOrders] = defaultdict(lambda: DayOrders(date=""))
    for ev in events:
        if ev.get("status") != "filled":
            continue
        symbol = ev.get("symbolValue") or ev.get("symbolPermtick") or ev.get("symbol", "")
        symbol = str(symbol).strip().upper()
        direction = str(ev.get("direction", "")).lower()
        ts = ev.get("time")
        if not symbol or not direction or ts is None:
            continue
        # `time` is epoch seconds (UTC). Convert to America/New_York date.
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        # Lean's schedule fires at 09:31 ET (14:31 UTC); a 5-hour shift is
        # close enough as a naive ET-date approximation for daily aggregation.
        # We just need to group by the trading day; ET is 4-5 hours behind UTC.
        # Subtracting 5 hours puts both 14:31 UTC (winter) and 13:31 UTC
        # (summer DST) onto the correct ET calendar date.
        et_date = dt.replace(tzinfo=None) - _five_hours()
        date_iso = et_date.strftime("%Y-%m-%d")

        d = by_date[date_iso]
        d.date = date_iso
        if direction == "buy":
            d.adds.add(symbol)
        elif direction == "sell":
            d.removes.add(symbol)
    return dict(by_date)


def _five_hours():
    from datetime import timedelta
    return timedelta(hours=5)


# ---------------------------------------------------------------------------
# Diff + report
# ---------------------------------------------------------------------------


@dataclass
class DayDiff:
    date: str
    adds_match: bool
    removes_match: bool
    expected_adds: set[str]
    actual_adds: set[str]
    expected_removes: set[str]
    actual_removes: set[str]


def diff_orders(
    expected: dict[str, DayOrders],
    actual: dict[str, DayOrders],
) -> list[DayDiff]:
    all_dates = sorted(set(expected.keys()) | set(actual.keys()))
    diffs: list[DayDiff] = []
    for date in all_dates:
        e = expected.get(date, DayOrders(date=date))
        a = actual.get(date, DayOrders(date=date))
        diffs.append(DayDiff(
            date=date,
            adds_match=(e.adds == a.adds),
            removes_match=(e.removes == a.removes),
            expected_adds=e.adds,
            actual_adds=a.adds,
            expected_removes=e.removes,
            actual_removes=a.removes,
        ))
    return diffs


def print_report(diffs: list[DayDiff], max_mismatch_rows: int = 10) -> bool:
    n_total = len(diffs)
    n_full_match = sum(1 for d in diffs if d.adds_match and d.removes_match)
    n_adds_match = sum(1 for d in diffs if d.adds_match)
    n_removes_match = sum(1 for d in diffs if d.removes_match)

    # Jaccard similarity per day — accounts for partial overlaps which the
    # set-equality check above is blind to. (1.0 = perfect, 0.0 = disjoint.)
    def _jaccard(a: set[str], b: set[str]) -> float:
        if not a and not b:
            return 1.0
        return len(a & b) / max(1, len(a | b))

    adds_j = [_jaccard(d.expected_adds, d.actual_adds) for d in diffs]
    rem_j = [_jaccard(d.expected_removes, d.actual_removes) for d in diffs]

    print("=" * 80)
    print("M3 PARITY: expected (TopkDropoutStrategy on signals.csv) vs Lean actual")
    print("=" * 80)
    print(f"  total trading days compared: {n_total}")
    print(f"  full match (adds + removes): {n_full_match} ({n_full_match/n_total:.1%})")
    print(f"  adds match:                  {n_adds_match} ({n_adds_match/n_total:.1%})")
    print(f"  removes match:               {n_removes_match} ({n_removes_match/n_total:.1%})")
    print(f"  avg adds Jaccard:            {sum(adds_j)/n_total:.3f}")
    print(f"  avg removes Jaccard:         {sum(rem_j)/n_total:.3f}")

    # Universe-level: total distinct (date, symbol) trades each side made.
    total_expected_trades = sum(len(d.expected_adds) + len(d.expected_removes) for d in diffs)
    total_actual_trades = sum(len(d.actual_adds) + len(d.actual_removes) for d in diffs)
    total_shared_trades = sum(
        len(d.expected_adds & d.actual_adds) + len(d.expected_removes & d.actual_removes)
        for d in diffs
    )
    print(f"  expected (date,sym,side) trades: {total_expected_trades}")
    print(f"  actual   (date,sym,side) trades: {total_actual_trades}")
    print(f"  shared:                          {total_shared_trades} "
          f"({total_shared_trades/max(total_expected_trades,1):.1%} of expected, "
          f"{total_shared_trades/max(total_actual_trades,1):.1%} of actual)")

    # When did the first divergence happen? Useful for debugging cascade drift.
    first_mismatch = next((d for d in diffs if not (d.adds_match and d.removes_match)), None)
    if first_mismatch:
        print(f"  first mismatch:              {first_mismatch.date}")

    mismatches = [d for d in diffs if not (d.adds_match and d.removes_match)]
    if not mismatches:
        print()
        print("  VERDICT: PERFECT PARITY")
        return True

    print()
    print(f"  {len(mismatches)} mismatch days. Showing first {min(max_mismatch_rows, len(mismatches))}:")
    print()
    for d in mismatches[:max_mismatch_rows]:
        print(f"  --- {d.date} ---")
        if not d.adds_match:
            only_e = d.expected_adds - d.actual_adds
            only_a = d.actual_adds - d.expected_adds
            print(f"    adds:    expected={sorted(d.expected_adds)}")
            print(f"             actual=  {sorted(d.actual_adds)}")
            if only_e:
                print(f"             missing in Lean: {sorted(only_e)}")
            if only_a:
                print(f"             extra in Lean:   {sorted(only_a)}")
        if not d.removes_match:
            only_e = d.expected_removes - d.actual_removes
            only_a = d.actual_removes - d.expected_removes
            print(f"    removes: expected={sorted(d.expected_removes)}")
            print(f"             actual=  {sorted(d.actual_removes)}")
            if only_e:
                print(f"             missing in Lean: {sorted(only_e)}")
            if only_a:
                print(f"             extra in Lean:   {sorted(only_a)}")

    # Acceptance: ≥95% full-match per ROADMAP.
    return n_full_match / n_total >= 0.95


# ---------------------------------------------------------------------------
# Discovery + CLI
# ---------------------------------------------------------------------------


def _newest_lean_backtest_dir(project_dir: Path) -> Path | None:
    backtests = project_dir / "backtests"
    if not backtests.exists():
        return None
    runs = [d for d in backtests.iterdir() if d.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda d: d.stat().st_mtime)


def _find_order_events_json(lean_run_dir: Path) -> Path | None:
    candidates = list(lean_run_dir.glob("*-order-events.json"))
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-config", required=True,
                        help="M1 baseline YAML, e.g. dow30_lightgbm.yaml")
    parser.add_argument("--signals", type=Path, default=None,
                        help="Signal CSV path. Default: lean_projects/<project>/signals.csv")
    parser.add_argument("--lean-run", type=Path, default=None,
                        help="Lean backtest dir. Default: newest under <project>/backtests/")
    parser.add_argument("--lean-project", default=None,
                        help="Lean project name. Default: dipdiver_<m1-config-stem>")
    parser.add_argument("--max-mismatch-rows", type=int, default=10)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Resolve paths.
    cfg_dir = repo_root() / "dipdiver" / "brain" / "baselines" / "configs"
    m1 = load_m1_config(cfg_dir / args.m1_config)
    topk = int(m1.backtest_params.get("topk", 10))
    n_drop = int(m1.backtest_params.get("n_drop", 3))

    project_name = args.lean_project or f"dipdiver_{args.m1_config.replace('.yaml','')}"
    project_dir = repo_root() / "lean_projects" / project_name
    if not project_dir.exists():
        print(f"[m3-verify] ERROR: Lean project not found at {project_dir}")
        return 1

    signals_csv = args.signals or (project_dir / "signals.csv")
    if not signals_csv.exists():
        print(f"[m3-verify] ERROR: signals.csv not found at {signals_csv}")
        print(f"[m3-verify] Generate it: python scripts/m3_export_signals.py --m1-config {args.m1_config}")
        return 1

    lean_run = args.lean_run or _newest_lean_backtest_dir(project_dir)
    if lean_run is None:
        print(f"[m3-verify] ERROR: no backtest runs found under {project_dir}/backtests/")
        return 1
    events_json = _find_order_events_json(lean_run)
    if events_json is None:
        print(f"[m3-verify] ERROR: no *-order-events.json file in {lean_run}")
        return 1

    print(f"[m3-verify] M1 config:     {args.m1_config}  (topk={topk}, n_drop={n_drop})")
    print(f"[m3-verify] signals CSV:   {signals_csv}")
    print(f"[m3-verify] Lean run:      {lean_run}")
    print(f"[m3-verify] order events:  {events_json.name}")
    print()

    expected = simulate_expected_orders(signals_csv, topk=topk, n_drop=n_drop)
    actual = parse_lean_orders(events_json)
    log.info("expected: %d rotation days, actual: %d rotation days",
             len(expected), len(actual))

    diffs = diff_orders(expected, actual)
    ok = print_report(diffs, max_mismatch_rows=args.max_mismatch_rows)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
