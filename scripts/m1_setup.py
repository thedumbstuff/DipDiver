"""M1 data setup — fetch OHLCV for all baseline universes and dump to Qlib.

Run once per machine. Idempotent: re-running skips universes whose Qlib store
already exists unless --force is passed.

Outputs:
  ~/.qlib/qlib_data/us_data/      (DOW 30, from Qlib's prebuilt bundle if available)
  ~/.qlib/qlib_data/in_data/      (NIFTY 50, from Yahoo)
  ~/.qlib/qlib_data/crypto_data/  (BTC/ETH/SOL, from Yahoo)

Usage:
  python scripts/m1_setup.py                       # all three universes
  python scripts/m1_setup.py --universe dow30      # one universe
  python scripts/m1_setup.py --force               # re-fetch even if present

After this, run scripts/m1_verify.py to confirm the data is sane.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dipdiver._paths import data_root
from dipdiver.brain.baselines.data import fetch_and_dump
from dipdiver.brain.baselines.universes import UNIVERSES, get_universe

DEFAULT_DATA_ROOT = data_root()

# Fetch windows extend past test_end so Qlib's backtest has calendar lookahead
# (TopkDropoutStrategy looks up day-after-trade for rebalances).
FETCH_WINDOWS: dict[str, tuple[str, str]] = {
    "dow30":         ("2013-01-01", "2026-06-01"),
    "sp500":         ("2013-01-01", "2026-06-01"),
    "nifty50":       ("2013-01-01", "2026-06-01"),
    "crypto":        ("2018-01-01", "2026-06-01"),
    "world_indices": ("2013-01-01", "2026-06-01"),
}

# sp500 shares us_data with dow30 (see sp500_*.yaml qlib_provider_uri). If
# us_data was already built for dow30 only, re-run with
# `--universe sp500 --force` to pull the extra constituents into the store.
PROVIDER_DIR: dict[str, str] = {
    "dow30":         "us_data",
    "sp500":         "us_data",
    "nifty50":       "in_data",
    "crypto":        "crypto_data",
    "world_indices": "world_data",
}


def _already_set_up(provider_uri: Path) -> bool:
    return (provider_uri / "calendars" / "day.txt").exists() and any(
        (provider_uri / "features").glob("*")
    )


def setup_one(universe_name: str, data_root: Path, force: bool) -> None:
    universe = get_universe(universe_name)
    provider_uri = data_root / PROVIDER_DIR[universe_name]
    if _already_set_up(provider_uri) and not force:
        print(f"[skip] {universe_name}: store already at {provider_uri} (use --force to refetch)")
        return
    start, end = FETCH_WINDOWS[universe_name]
    print(f"[fetch] {universe_name}: {start} -> {end} -> {provider_uri}")
    results = fetch_and_dump(universe, provider_uri, start, end)
    n = len(results)
    rows = sum(r.rows for r in results if r.rows >= 0)
    missing = sum(r.missing_days for r in results)
    print(f"[done]  {universe_name}: {n} instruments, {rows} rows, {missing} missing day-cells")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe",
        choices=tuple(UNIVERSES) + ("all",),
        default="all",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    targets = list(UNIVERSES) if args.universe == "all" else [args.universe]
    for name in targets:
        setup_one(name, args.data_root, args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
