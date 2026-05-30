"""M1 baseline runner — execute all six baselines, print metrics, optionally lock.

Iterates over the six shipped configs, runs each, prints a summary table, and
(with --lock) writes locked result files to dipdiver/brain/baselines/locked/.

Usage:
  python scripts/m1_run.py                       # run all six, print only
  python scripts/m1_run.py --config dow30_lightgbm.yaml
  python scripts/m1_run.py --lock                # also persist as new locks
  python scripts/m1_run.py --verify              # compare against existing locks
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dipdiver.brain.baselines.config import load_config
from dipdiver.brain.baselines.results import BaselineResult, compare, load_locked, save_locked
from dipdiver.brain.baselines.runner import run_baseline

CONFIG_DIR = Path(__file__).resolve().parent.parent / "dipdiver" / "brain" / "baselines" / "configs"

ALL_CONFIGS = (
    "dow30_lightgbm.yaml",
    "dow30_lstm.yaml",
    "nifty50_lightgbm.yaml",
    "nifty50_lstm.yaml",
    "crypto_lightgbm.yaml",
    "crypto_lstm.yaml",
    "world_indices_lightgbm.yaml",
    "world_indices_lstm.yaml",
)


def _print_row(result: BaselineResult, status: str = "") -> None:
    print(
        f"{result.config_name:<22}  "
        f"sharpe={result.sharpe:+.3f}  "
        f"ann_ret={result.annualised_return:+.2%}  "
        f"max_dd={result.max_drawdown:+.2%}  "
        f"hit={result.hit_rate:.2%}  "
        f"excess={result.excess_return:+.2%}  "
        f"{status}"
    )


def run_one(
    config_filename: str,
    do_lock: bool,
    do_verify: bool,
) -> tuple[BaselineResult, str]:
    config = load_config(CONFIG_DIR / config_filename)
    result = run_baseline(config)
    status = ""

    if do_lock:
        try:
            path = save_locked(result)
            status = f"locked->{path.name}"
        except FileExistsError:
            status = "lock-exists"

    if do_verify:
        try:
            locked = load_locked(result.config_hash)
        except FileNotFoundError:
            status = "no-lock"
        else:
            ok = compare(result, locked)
            status = "verify=PASS" if ok else "verify=FAIL"

    return result, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        help="filename under configs/ (repeatable). Default: all six.",
    )
    parser.add_argument("--lock", action="store_true", help="persist results as new locks")
    parser.add_argument("--verify", action="store_true", help="compare against existing locks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    targets = args.config if args.config else list(ALL_CONFIGS)
    failures: list[str] = []
    print("=" * 100)
    for filename in targets:
        try:
            result, status = run_one(filename, args.lock, args.verify)
        except Exception as e:  # noqa: BLE001
            print(f"{filename}: ERROR {type(e).__name__}: {e}")
            failures.append(filename)
            continue
        _print_row(result, status)
        if args.verify and "FAIL" in status:
            failures.append(filename)
    print("=" * 100)
    if failures:
        print(f"FAILED: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
