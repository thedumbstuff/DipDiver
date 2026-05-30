"""M1 data verification — print sanity reports for each universe.

Run after scripts/m1_setup.py. Reads the on-disk Qlib stores back, checks
that every expected instrument is present, that the calendar covers the
expected span, and that no feature file is silently NaN-only.

Exits non-zero if any universe fails verification, so this can also gate CI.

Usage:
  python scripts/m1_verify.py                # all three universes
  python scripts/m1_verify.py --universe dow30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dipdiver._paths import data_root
from dipdiver.brain.baselines.config import load_config
from dipdiver.brain.baselines.data import print_report, verify_store
from dipdiver.brain.baselines.universes import UNIVERSES, get_universe

DEFAULT_DATA_ROOT = data_root()
PROVIDER_DIR: dict[str, str] = {
    "dow30":         "us_data",
    "nifty50":       "in_data",
    "crypto":        "crypto_data",
    "world_indices": "world_data",
}
CONFIG_DIR = Path(__file__).resolve().parent.parent / "dipdiver" / "brain" / "baselines" / "configs"


def _latest_test_end(universe_name: str) -> str | None:
    """Scan configs for this universe; return the latest test_end."""
    ends: list[str] = []
    for cfg_path in CONFIG_DIR.glob("*.yaml"):
        try:
            cfg = load_config(cfg_path)
        except Exception:  # noqa: BLE001
            continue
        if cfg.universe == universe_name:
            ends.append(cfg.test_end)
    return max(ends) if ends else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--universe",
        choices=tuple(UNIVERSES) + ("all",),
        default="all",
    )
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args(argv)

    targets = list(UNIVERSES) if args.universe == "all" else [args.universe]
    all_ok = True
    for name in targets:
        universe = get_universe(name)
        provider_uri = args.data_root / PROVIDER_DIR[name]
        try:
            report = verify_store(
                provider_uri,
                universe,
                min_required_end=_latest_test_end(name),
            )
        except FileNotFoundError as e:
            print(f"\n=== {name} @ {provider_uri} ===")
            print(f"  ERROR: {e}")
            print("  hint: run scripts/m1_setup.py first")
            all_ok = False
            continue
        print_report(report)
        all_ok = all_ok and report.ok

    print()
    if all_ok:
        print("ALL UNIVERSES OK — proceed to scripts/m1_run.py")
        return 0
    print("VERIFICATION FAILED — inspect missing/gap rows above before running baselines")
    return 1


if __name__ == "__main__":
    sys.exit(main())
