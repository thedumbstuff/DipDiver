"""M6 nightly entry point — run M3 live + promote to scoreboard in one shot.

This is the canonical command you should put in a scheduler (cron, GitHub
Actions, Windows Task Scheduler) once you trust the pipeline. For now, run it
manually after market open (or with --force to test off-hours).

Pipeline:
    1. m3_live_alpaca.run_once  (compute target, submit orders, write run record)
    2. m6_backfill.main         (read all m3_live records, append new ones to scoreboard.jsonl)
    3. (future) fetch T-1 P&L from Alpaca and write a PnlSettledEvent
    4. (future) render docs/SCOREBOARD.md and commit

Usage:
    python scripts/m6_nightly.py --m1-config dow30_lightgbm.yaml --with-committee
    python scripts/m6_nightly.py --m1-config dow30_lightgbm.yaml --dry-run

Any flag accepted by m3_live_alpaca.py is supported.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dipdiver._paths import repo_root


log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-config", required=True,
                        help="M1 baseline YAML, e.g. dow30_lightgbm.yaml")
    parser.add_argument("--signals", type=Path, default=None,
                        help="Signal CSV path. Default: data/signals/<m1-config-stem>.csv")
    parser.add_argument("--dry-run", action="store_true",
                        help="Plan orders but don't submit. Run record + scoreboard still written.")
    parser.add_argument("--force", action="store_true",
                        help="Run even when the market is closed")
    parser.add_argument("--with-committee", action="store_true",
                        help="Route proposed BUYs through the M5 committee")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Run record output dir. Default: logs/m3_live/<universe>")
    parser.add_argument("--scoreboard", type=Path, default=None,
                        help="Scoreboard JSONL path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    print("=" * 78)
    print(" M6 NIGHTLY")
    print(f"   config:    {args.m1_config}")
    print(f"   committee: {'ON' if args.with_committee else 'OFF'}")
    print(f"   dry-run:   {args.dry_run}")
    print("=" * 78)
    print()

    # Step 1: run m3_live_alpaca
    print("[m6-nightly] step 1/2: m3_live_alpaca")
    print("-" * 78)
    from scripts.m3_live_alpaca import main as m3_main
    m3_argv = ["--m1-config", args.m1_config]
    if args.signals:
        m3_argv += ["--signals", str(args.signals)]
    if args.dry_run:
        m3_argv += ["--dry-run"]
    if args.force:
        m3_argv += ["--force"]
    if args.with_committee:
        m3_argv += ["--with-committee"]
    if args.output_dir:
        m3_argv += ["--output-dir", str(args.output_dir)]
    if args.verbose:
        m3_argv += ["--verbose"]
    rc = m3_main(m3_argv)
    if rc != 0:
        print(f"[m6-nightly] m3_live_alpaca exited with rc={rc}; aborting before scoreboard write")
        return rc

    print()
    print("[m6-nightly] step 2/2: m6_backfill (promote new run records)")
    print("-" * 78)
    from scripts.m6_backfill import main as backfill_main
    bf_argv: list[str] = []
    if args.scoreboard:
        bf_argv += ["--scoreboard", str(args.scoreboard)]
    if args.verbose:
        bf_argv += ["--verbose"]
    rc = backfill_main(bf_argv)
    if rc != 0:
        print(f"[m6-nightly] m6_backfill exited with rc={rc}")
        return rc

    print()
    print("[m6-nightly] done.")
    print("   render: python scripts/m6_render_scoreboard.py")
    return 0


if __name__ == "__main__":
    # Ensure `scripts/` is importable when running this file directly.
    sys.path.insert(0, str(repo_root()))
    sys.exit(main())
