"""Inspect an m2-lite run directory.

Default: locate the newest run under logs/m2_lite/, print the summary, and
list every loop one line each.

Usage:
    python scripts/m2_lite_verify.py
    python scripts/m2_lite_verify.py --run logs/m2_lite/dow30_lightgbm_20260602_154606
    python scripts/m2_lite_verify.py --detail                  # full per-loop dump
    python scripts/m2_lite_verify.py --loop 2                  # one loop's full record
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dipdiver._paths import ui_logs_dir


def _newest_run() -> Path | None:
    root = ui_logs_dir() / "m2_lite"
    if not root.exists():
        return None
    runs = [d for d in root.iterdir() if d.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda d: d.stat().st_mtime)


def _load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _print_summary(summary: dict) -> None:
    universe = summary.get("universe", "?")
    m1_config = summary.get("m1_config_name", "?")
    benchmark = summary.get("benchmark", "?")
    region = summary.get("region", "?")
    test_window = summary.get("test_window", {})

    print("=" * 80)
    print(f"M2-LITE SUMMARY  ({summary.get('timestamp_utc', 'unknown')})")
    print("=" * 80)
    print(f"  Universe:    {universe}  (region: {region})")
    print(f"  M1 config:   {m1_config}")
    print(f"  Benchmark:   {benchmark}")
    if test_window:
        print(f"  Test window: {test_window.get('start', '?')} -> {test_window.get('end', '?')}")
    print()
    print(f"  loops run:        {summary['n_loops_run']}")
    print(f"  loops successful: {summary['n_loops_successful']}")
    print(f"  total cost:       ${summary['total_cost_usd']:.3f}  "
          f"({summary['total_input_tokens']:,} in + {summary['total_output_tokens']:,} out)")

    m1 = summary.get("m1_baseline", {})
    if m1:
        print(f"\n  M1 baseline:")
        print(f"    Sharpe:           {m1.get('sharpe', float('nan')):+.3f}")
        print(f"    Annualised return:{m1.get('annualised_return', float('nan')):+.2%}")
        if m1.get("config_hash"):
            print(f"    config hash:      {m1['config_hash']}")

    best = summary.get("best")
    if not best:
        print("\n  no successful loops — see per-loop dump for errors")
        return

    print(f"\n  Best loop: #{summary['best_loop_index']}")
    print(f"    {'metric':<22} {'M2 best':>10} {'delta vs M1':>14}")
    print(f"    {'-' * 48}")
    print(f"    {'Sharpe':<22} {best['sharpe']:>+10.3f} {best['delta_sharpe_vs_m1']:>+14.3f}")
    print(f"    {'Annualised return':<22} {best['annualised_return']:>+10.2%} "
          f"{best['delta_ann_return_vs_m1']:>+14.2%}")
    if "max_drawdown" in best:
        print(f"    {'Max drawdown':<22} {best['max_drawdown']:>+10.2%}")
    if "excess_return" in best:
        print(f"    {'Excess vs benchmark':<22} {best['excess_return']:>+10.2%}")

    verdict = "BEATS M1" if best["delta_sharpe_vs_m1"] > 0 else "does NOT beat M1"
    print(f"\n  Verdict: {verdict}")

    print(f"\n  Best hypothesis:")
    for line in best.get("hypothesis", "").splitlines() or [""]:
        print(f"    {line}")
    print(f"\n  Best factors:")
    for f in best.get("factors", []):
        print(f"    {f['name']}  :=  {f['expression']}")


def _print_loop_summary_table(loops: list[dict]) -> None:
    print("\n" + "=" * 80)
    print(f"{'LOOP':<5} {'STATUS':<10} {'SHARPE':>9} {'ANN_RET':>9} {'COST':>8} {'FACTORS'}")
    print("-" * 80)
    for r in loops:
        status = "ok" if r.get("metrics") else "FAIL"
        sharpe = r["metrics"]["sharpe"] if r.get("metrics") else None
        ann = r["metrics"]["annualised_return"] if r.get("metrics") else None
        cost = r.get("llm_cost_usd", 0.0)
        names = ", ".join(f["name"] for f in (r.get("proposal") or {}).get("factors", []))
        sharpe_s = f"{sharpe:+.3f}" if sharpe is not None else "  —  "
        ann_s = f"{ann:+.2%}" if ann is not None else "  —  "
        print(f"{r['index']:<5} {status:<10} {sharpe_s:>9} {ann_s:>9} ${cost:>6.3f}  {names[:40]}")


def _print_loop_detail(rec: dict) -> None:
    print("=" * 80)
    print(f"Loop {rec['index']}  status={'ok' if rec.get('metrics') else 'FAIL'}")
    print("=" * 80)
    if rec.get("error"):
        print(f"  ERROR: {rec['error']}")
    p = rec.get("proposal")
    if p:
        print(f"\n  Hypothesis:")
        for line in p.get("hypothesis", "").splitlines():
            print(f"    {line}")
        if p.get("market_thesis"):
            print(f"\n  Market thesis:")
            for line in p["market_thesis"].splitlines():
                print(f"    {line}")
        print(f"\n  Factors:")
        for f in p.get("factors", []):
            print(f"    {f['name']}  :=  {f['expression']}")
            if f.get("rationale"):
                print(f"      -> {f['rationale']}")
    m = rec.get("metrics")
    if m:
        print(f"\n  Backtest:")
        print(f"    Sharpe              = {m['sharpe']:+.3f}")
        print(f"    Annualised return   = {m['annualised_return']:+.2%}")
        print(f"    Annualised vol      = {m['annualised_volatility']:+.2%}")
        print(f"    Max drawdown        = {m['max_drawdown']:+.2%}")
        print(f"    Turnover (annual)   = {m['turnover']:+.2f}")
        print(f"    Hit rate            = {m['hit_rate']:.2%}")
        print(f"    n trades            = {m['n_trades']}")
        print(f"    Benchmark ann ret   = {m['benchmark_annualised_return']:+.2%}")
        print(f"    Excess vs bench     = {m['excess_return']:+.2%}")
    print(f"\n  Tokens: in={rec.get('llm_input_tokens', 0):,} "
          f"out={rec.get('llm_output_tokens', 0):,}  "
          f"Cost: ${rec.get('llm_cost_usd', 0.0):.3f}  "
          f"Wall: {rec.get('wall_seconds', 0.0):.1f}s")


def _list_runs() -> int:
    """Print one line per m2-lite run found under logs/m2_lite/, newest first."""
    root = ui_logs_dir() / "m2_lite"
    if not root.exists():
        print("[m2-lite-verify] no logs/m2_lite/ directory yet")
        return 1
    runs = sorted(
        (d for d in root.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not runs:
        print("[m2-lite-verify] no runs under logs/m2_lite/")
        return 1
    header = f"{'#':<3} {'RUN DIR':<55} {'UNIV':<14} {'OK':<5} {'BEST SHARPE':>11} {'vs M1':>9} {'COST':>7}"
    print(header)
    print("-" * len(header))
    for i, d in enumerate(runs):
        summary = _load_json(d / "summary.json") or {}
        univ = summary.get("universe") or _universe_from_dirname(d.name)
        nok = f"{summary.get('n_loops_successful', '?')}/{summary.get('n_loops_run', '?')}"
        best = summary.get("best") or {}
        sharpe = f"{best.get('sharpe', float('nan')):+.3f}" if best else "    —    "
        delta = f"{best.get('delta_sharpe_vs_m1', float('nan')):+.3f}" if best else "  —  "
        cost = f"${summary.get('total_cost_usd', 0.0):.3f}"
        print(f"{i:<3} {d.name:<55} {univ:<14} {nok:<5} {sharpe:>11} {delta:>9} {cost:>7}")
    return 0


def _universe_from_dirname(name: str) -> str:
    """Best-effort: dirs are named '{m1_config_stem}_{timestamp}'."""
    parts = name.rsplit("_", 2)  # split off the YYYYMMDD_HHMMSS suffix
    if len(parts) < 3:
        return "?"
    stem = parts[0]
    # stem is like "dow30_lightgbm" or "world_indices_lightgbm"
    for kind in ("_lightgbm", "_lstm"):
        if stem.endswith(kind):
            return stem[: -len(kind)]
    return stem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=None,
                        help="Specific run dir; default: newest under logs/m2_lite/")
    parser.add_argument("--list", action="store_true",
                        help="List all m2-lite runs and exit (universe + best Sharpe + delta + cost)")
    parser.add_argument("--detail", action="store_true",
                        help="Full per-loop dump (hypothesis, factors, backtest, costs)")
    parser.add_argument("--loop", type=int, default=None,
                        help="Print full record for a single loop index only")
    args = parser.parse_args(argv)

    if args.list:
        return _list_runs()

    run_dir = args.run or _newest_run()
    if run_dir is None or not run_dir.exists():
        print("[m2-lite-verify] no m2-lite run found under logs/m2_lite/")
        return 1
    # The directory name itself carries the M1 config slug for old runs that
    # don't yet have the explicit field in summary.json — show both.
    print(f"[m2-lite-verify] run dir: {run_dir.name}")

    summary = _load_json(run_dir / "summary.json")
    if summary is None:
        print(f"[m2-lite-verify] summary.json missing — run may have been killed mid-loop")
    else:
        _print_summary(summary)

    loop_files = sorted(run_dir.glob("loop_*.json"),
                        key=lambda p: int(p.stem.split("_")[1]))
    if not loop_files:
        print("[m2-lite-verify] no loop_*.json files in run dir")
        return 1
    loops = [json.loads(p.read_text(encoding="utf-8")) for p in loop_files]

    if args.loop is not None:
        match = next((r for r in loops if r["index"] == args.loop), None)
        if match is None:
            print(f"[m2-lite-verify] no loop_{args.loop}.json in this run")
            return 1
        print()
        _print_loop_detail(match)
        return 0

    _print_loop_summary_table(loops)
    if args.detail:
        for r in loops:
            print()
            _print_loop_detail(r)

    return 0


if __name__ == "__main__":
    sys.exit(main())
