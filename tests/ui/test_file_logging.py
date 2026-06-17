"""App logs are written to a file under ui_logs_dir() so the /logs page can show
them (basicConfig only reaches stderr). The handler is attached to the root
logger, which is global state, so each test restores the root handlers it added.
"""

from __future__ import annotations

import contextlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dipdiver.ui.logging_setup import LOG_FILENAME, setup_file_logging


@contextlib.contextmanager
def _isolated_root_handlers():
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level
    try:
        yield root
    finally:
        for h in list(root.handlers):
            if h not in before:
                root.removeHandler(h)
                with contextlib.suppress(Exception):
                    h.close()
        root.setLevel(level)


def test_setup_file_logging_writes_app_logs_under_ui_logs_dir(data_root: Path):
    with _isolated_root_handlers():
        path = setup_file_logging("INFO")
        assert path is not None
        log_file = data_root / "logs" / LOG_FILENAME
        assert log_file.exists()

        logging.getLogger("dipdiver.filelogtest").info("MARKER-file-logging-7321")
        for h in logging.getLogger().handlers:
            with contextlib.suppress(Exception):
                h.flush()
        assert "MARKER-file-logging-7321" in log_file.read_text(encoding="utf-8")


def test_setup_file_logging_is_idempotent(data_root: Path):
    with _isolated_root_handlers() as root:
        setup_file_logging()
        setup_file_logging()
        n = sum(isinstance(h, RotatingFileHandler) for h in root.handlers)
        assert n == 1  # no duplicate handler on a second call


def test_logs_page_lists_the_app_log(client, data_root: Path):
    # The route reads ui_logs_dir(); writing the app log makes it appear.
    with _isolated_root_handlers():
        setup_file_logging("INFO")
        logging.getLogger("dipdiver.filelogtest").info("MARKER-on-page-9988")
        for h in logging.getLogger().handlers:
            with contextlib.suppress(Exception):
                h.flush()
        r = client.get("/logs")
        assert r.status_code == 200
        assert LOG_FILENAME in r.text
