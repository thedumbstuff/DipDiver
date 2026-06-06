"""M6 nightly entry point — run M3 live + promote to scoreboard in one shot.

This is the canonical command you should put in a scheduler (cron, GitHub
Actions, Windows Task Scheduler) once you trust the pipeline. For now, run it
manually after market open (or with --force to test off-hours).

Pipeline:
    1. m3_live_alpaca.run_once  (compute target, submit orders, write run record)
    2. m6_backfill.main         (read all m3_live records, append new ones to scoreboard.jsonl)
    3. pnl_settle.run           (settle yesterday's P&L into scoreboard.jsonl)
    4. veto_backfill.run        (write T+5 counterfactuals for past committee vetoes)
    5. scoreboard_render.run    (write rendered/SCOREBOARD.md so it's fresh by morning)

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
    print("[m6-nightly] step 2/5: m6_backfill (promote new run records)")
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
    print("[m6-nightly] step 3/5: pnl_settle (settle yesterday's P&L)")
    print("-" * 78)
    overall_rc = 0
    try:
        from dipdiver.ui.jobs import pnl_settle
        result = pnl_settle.run()
        print(f"   {result.get('message', result)}")
        if result.get("rc", 0) != 0:
            overall_rc = result["rc"]
            print(f"[m6-nightly] pnl_settle non-zero rc; continuing to remaining steps")
    except Exception as e:  # noqa: BLE001
        log.exception("pnl_settle failed")
        print(f"[m6-nightly] pnl_settle crashed: {type(e).__name__}: {e}; continuing")
        overall_rc = overall_rc or 1

    print()
    print("[m6-nightly] step 4/5: veto_backfill (T+5 counterfactuals for past vetoes)")
    print("-" * 78)
    try:
        from dipdiver.ui.jobs import veto_backfill
        result = veto_backfill.run()
        print(f"   {result.get('message', result)}")
        if result.get("rc", 0) != 0 and overall_rc == 0:
            overall_rc = result["rc"]
    except Exception as e:  # noqa: BLE001
        log.exception("veto_backfill failed")
        print(f"[m6-nightly] veto_backfill crashed: {type(e).__name__}: {e}; continuing")
        overall_rc = overall_rc or 1

    print()
    print("[m6-nightly] step 5/5: scoreboard_render (refresh SCOREBOARD.md)")
    print("-" * 78)
    try:
        from dipdiver.ui.jobs import scoreboard_render
        result = scoreboard_render.run()
        print(f"   rendered {result.get('rows_rendered', 0)} rows -> {result.get('output_path', '?')}")
    except Exception as e:  # noqa: BLE001
        log.exception("scoreboard_render failed")
        print(f"[m6-nightly] scoreboard_render crashed: {type(e).__name__}: {e}")
        overall_rc = overall_rc or 1

    print()
    print(f"[m6-nightly] done. (overall rc={overall_rc})")
    return overall_rc


if __name__ == "__main__":
    # Ensure `scripts/` is importable when running this file directly.
    sys.path.insert(0, str(repo_root()))
    sys.exit(main())
