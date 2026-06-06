"""nightly_run — wraps scripts/m6_nightly.main for each enabled strategy."""

from __future__ import annotations

import logging

from dipdiver.ui.settings import ui_config


log = logging.getLogger(__name__)


def run() -> dict:
    """Invoke m6_nightly for every enabled strategy.

    Returns a summary dict consumed by the scheduler hook (which writes a
    JobLog row + optionally pushes a Telegram alert).
    """
    import sys
    from dipdiver._paths import repo_root, ui_data_root
    p = str(repo_root())
    if p not in sys.path:
        sys.path.insert(0, p)
    from scripts.m6_nightly import main as m6_main

    # Stage 4 / M11 — honour the shell kill switch.
    flag = ui_data_root() / "DIPDIVER_KILLED"
    if flag.exists():
        log.warning("nightly skipped: kill flag present at %s", flag)
        return {
            "rc": 0,
            "skipped": True,
            "message": f"DIPDIVER_KILLED flag present; nightly aborted. Remove {flag} to resume.",
        }

    cfg = ui_config()
    results: list[dict] = []
    overall_rc = 0
    for s in cfg.strategies:
        if not s.enabled:
            continue
        argv = ["--m1-config", s.m1_config]
        if s.with_committee:
            argv.append("--with-committee")
        log.info("nightly: %s (committee=%s)", s.strategy_id, s.with_committee)
        try:
            rc = m6_main(argv)
        except SystemExit as e:  # main() may use sys.exit
            rc = int(e.code or 0)
        except Exception as e:  # noqa: BLE001
            log.exception("nightly failed for %s", s.strategy_id)
            results.append({"strategy_id": s.strategy_id, "rc": 1, "error": str(e)})
            overall_rc = 1
            continue
        results.append({"strategy_id": s.strategy_id, "rc": rc})
        if rc != 0:
            overall_rc = rc

    return {
        "rc": overall_rc,
        "strategies_run": len(results),
        "results": results,
    }
