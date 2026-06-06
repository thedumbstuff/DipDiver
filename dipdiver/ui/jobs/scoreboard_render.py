"""scoreboard_render — write SCOREBOARD.md under ui_rendered_dir().

Per M8 decision 3, this stays on the VM only — never committed to repo.
"""

from __future__ import annotations

import logging

from dipdiver._paths import ui_rendered_dir
from dipdiver.harness.render import fuse_by_day, render_full_report
from dipdiver.harness.scoreboard import read_events


log = logging.getLogger(__name__)


def run() -> dict:
    out_dir = ui_rendered_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    events = read_events()
    rows = fuse_by_day(events)
    report = render_full_report(rows)
    out_path = out_dir / "SCOREBOARD.md"
    out_path.write_text(report, encoding="utf-8")
    log.info("scoreboard_render: %d rows -> %s", len(rows), out_path)
    return {
        "rc": 0,
        "rows_rendered": len(rows),
        "output_path": str(out_path),
    }
