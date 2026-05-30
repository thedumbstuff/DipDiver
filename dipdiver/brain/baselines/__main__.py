"""CLI: python -m dipdiver.brain.baselines --config <path> [--lock]"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dipdiver.brain.baselines.config import load_config
from dipdiver.brain.baselines.results import compare, load_locked, save_locked
from dipdiver.brain.baselines.runner import run_baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a DipDiver M1 baseline.")
    parser.add_argument("--config", type=Path, required=True, help="Path to YAML config")
    parser.add_argument(
        "--lock",
        action="store_true",
        help="Persist the result as the new lock for this config_hash (refuses to overwrite).",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Compare the result against the existing lock; exit non-zero on drift >5%%.",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = load_config(args.config)
    result = run_baseline(config)
    print(f"config_hash={result.config_hash}")
    print(f"sharpe={result.sharpe:.3f} ann_return={result.annualised_return:.3%}")
    print(f"max_dd={result.max_drawdown:.3%} hit_rate={result.hit_rate:.3%}")
    print(f"vs benchmark: excess_return={result.excess_return:+.3%}")

    if args.lock:
        path = save_locked(result)
        print(f"locked -> {path}")
    if args.verify:
        locked = load_locked(result.config_hash)
        ok = compare(result, locked)
        print(f"verify: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
