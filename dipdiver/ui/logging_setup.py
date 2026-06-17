"""Route the app's own logs to a file under ui_logs_dir() so the /logs page can
show them.

`logging.basicConfig` only writes to stderr (visible via `docker logs` /
journald, never on the /logs page). This attaches a RotatingFileHandler to the
root logger writing <DIPDIVER_UI_DATA_ROOT>/logs/dipdiver-ui.log, which the
/logs route lists and tails. App loggers (dipdiver.*) propagate to root, so job
runs, scheduler activity, and errors all land in the file.

Idempotent and best-effort: a second call (e.g. uvicorn --reload re-import, or
being called from both the CLI and the app lifespan) won't add a duplicate
handler, and a filesystem problem logs a warning rather than crashing startup.
"""

from __future__ import annotations

import contextlib
import logging
import os
from logging.handlers import RotatingFileHandler

from dipdiver._paths import ui_logs_dir

LOG_FILENAME = "dipdiver-ui.log"

_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def _as_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def setup_file_logging(level: str | int = "INFO") -> str | None:
    """Attach the rotating file handler to the root logger. Returns the log
    path on success, or None if it couldn't be set up."""
    root = logging.getLogger()
    lvl = _as_level(level)
    try:
        log_dir = ui_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        target = os.path.abspath(str(log_dir / LOG_FILENAME))
    except Exception:  # noqa: BLE001 — never let logging setup crash the app
        root.warning("file logging: could not prepare log dir", exc_info=True)
        return None

    # Keep a single dipdiver-ui.log handler. Same path -> already configured,
    # no-op. Different path (e.g. a changed data root, or per-test temp roots)
    # -> drop the stale one so handlers can't accumulate.
    for h in list(root.handlers):
        if isinstance(h, RotatingFileHandler) and os.path.basename(
            getattr(h, "baseFilename", "")
        ) == LOG_FILENAME:
            if getattr(h, "baseFilename", None) == target:
                return target
            root.removeHandler(h)
            with contextlib.suppress(Exception):
                h.close()

    try:
        handler = RotatingFileHandler(
            target, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        root.warning("file logging: could not open %s", target, exc_info=True)
        return None

    handler.setFormatter(logging.Formatter(_FORMAT))
    handler.setLevel(lvl)
    root.addHandler(handler)
    # basicConfig may not have run (e.g. uvicorn-only launch) — make sure the
    # root level lets `lvl` records through to the new handler.
    if root.level == logging.NOTSET or root.level > lvl:
        root.setLevel(lvl)
    root.info("file logging enabled -> %s", target)
    return target
