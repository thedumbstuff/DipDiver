"""Print the M6 scoreboard summary to stdout (or to a file with --out).

Usage:
    python scripts/m6_render_scoreboard.py
    python scripts/m6_render_scoreboard.py --out docs/SCOREBOARD.md
    python scripts/m6_render_scoreboard.py --universe dow30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dipdiver._paths import repo_root
from dipdiver.harness.render import fuse_by_day, render_full_report
from dipdiver.harness.scoreboard import (
    DEFAULT_SCOREBOARD_PATH,
    filter_events,
    read_events,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scoreboard", type=Path, default=None,
                        help=f"Path to scoreboard.jsonl (default: {DEFAULT_SCOREBOARD_PATH})")
    parser.add_argument("--out", type=Path, default=None,
                        help="Write rendered Markdown to this path (default: stdout)")
    parser.add_argument("--universe", default=None,
                        help="Filter to one universe")
    parser.add_argument("--strategy", default=None,
                        help="Filter to one strategy_id")
    args = parser.parse_args(argv)

    scoreboard_path = args.scoreboard or (repo_root() / DEFAULT_SCOREBOARD_PATH)
    events = read_events(scoreboard_path)
    if args.universe or args.strategy:
        events = filter_events(events, universe=args.universe, strategy_id=args.strategy)

    rows = fuse_by_day(events)
    report = render_full_report(rows)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(report, encoding="utf-8")
        print(f"wrote {args.out} ({len(report):,} chars, {len(rows)} rows)")
    else:
        # Use buffer.write to avoid the cp1252 codec failing on UTF-8 chars
        sys.stdout.buffer.write(report.encode("utf-8"))
        sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
