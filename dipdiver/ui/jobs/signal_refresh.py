"""signal_refresh — regenerate data/signals/*.csv for each enabled strategy."""

from __future__ import annotations

import logging
import sys

from dipdiver._paths import repo_root
from dipdiver.ui.settings import ui_config


log = logging.getLogger(__name__)


def _ensure_repo_on_path() -> None:
    """`scripts.*` lives at the repo root, not under the installed package.

    `dipdiver-ui` as a console_scripts entry has no repo-root entry on
    sys.path, so `from scripts.m3_export_signals import main` would fail.
    The app's lifespan injects it too, but adding it here makes the job
    importable in any context (cron, CLI, REPL).
    """
    p = str(repo_root())
    if p not in sys.path:
        sys.path.insert(0, p)


def run() -> dict:
    _ensure_repo_on_path()
    from scripts.m3_export_signals import main as export_main

    cfg = ui_config()
    results: list[dict] = []
    overall_rc = 0
    seen_configs: set[str] = set()
    for s in cfg.strategies:
        if not s.enabled or s.m1_config in seen_configs:
            continue
        seen_configs.add(s.m1_config)
        argv = ["--m1-config", s.m1_config]
        try:
            rc = export_main(argv)
        except SystemExit as e:
            rc = int(e.code or 0)
        except Exception as e:  # noqa: BLE001
            log.exception("signal_refresh failed for %s", s.m1_config)
            results.append({"m1_config": s.m1_config, "rc": 1, "error": str(e)})
            overall_rc = 1
            continue
        results.append({"m1_config": s.m1_config, "rc": rc})
        if rc != 0:
            overall_rc = rc

    return {"rc": overall_rc, "configs_run": len(results), "results": results}
