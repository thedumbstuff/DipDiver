"""Top-level test fixtures shared across harness/, ui/, brain/, adapters/.

The `data_root` fixture sets DIPDIVER_UI_DATA_ROOT to an isolated tmpdir and
resets module-level caches that read paths or env at import time. This lets
both UI and harness tests safely call `ui_scoreboard_path()` etc. without
polluting each other or the developer's working tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated data root per test."""
    monkeypatch.setenv("DIPDIVER_UI_DATA_ROOT", str(tmp_path))

    # Reset module-level caches that may have read paths or env at import time.
    try:
        import dipdiver.ui.settings as settings_mod
        settings_mod._ui_config_cache = None  # type: ignore[attr-defined]
        settings_mod.env_settings.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    try:
        import dipdiver.ui.db as db_mod
        db_mod._engine = None  # type: ignore[attr-defined]
        db_mod._SessionLocal = None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    try:
        import dipdiver.ui.jobs.scheduler as sched_mod
        if sched_mod._scheduler is not None and sched_mod._scheduler.running:
            sched_mod._scheduler.shutdown(wait=False)
        sched_mod._scheduler = None  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    return tmp_path
