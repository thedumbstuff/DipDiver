"""Tests for the foundation layer: paths, settings, DB lifecycle.

If any of these regress, every UI route silently writes to the wrong place
or shares state across operators.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# _paths.py — DIPDIVER_UI_DATA_ROOT overrides
# ---------------------------------------------------------------------------


def test_ui_data_root_defaults_to_repo_root(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DIPDIVER_UI_DATA_ROOT", raising=False)
    from dipdiver._paths import repo_root, ui_data_root
    assert ui_data_root() == repo_root()


def test_ui_data_root_respects_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DIPDIVER_UI_DATA_ROOT", str(tmp_path))
    from dipdiver._paths import ui_data_root
    assert ui_data_root() == tmp_path


def test_derived_paths_compose_under_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """All ui_*_path helpers must hang off ui_data_root()."""
    monkeypatch.setenv("DIPDIVER_UI_DATA_ROOT", str(tmp_path))
    from dipdiver._paths import (
        ui_config_path,
        ui_db_path,
        ui_logs_dir,
        ui_rendered_dir,
        ui_scoreboard_path,
        ui_schedules_path,
    )
    for p in (ui_config_path(), ui_db_path(), ui_logs_dir(),
              ui_rendered_dir(), ui_scoreboard_path(), ui_schedules_path()):
        try:
            p.relative_to(tmp_path)
        except ValueError:
            pytest.fail(f"path {p} does not live under {tmp_path}")


# ---------------------------------------------------------------------------
# settings.py — UiConfig YAML round-trip
# ---------------------------------------------------------------------------


def test_ui_config_default_when_file_missing(data_root: Path):
    from dipdiver.ui.settings import load_ui_config
    cfg = load_ui_config()
    # Defaults include the two dow30 strategies.
    assert len(cfg.strategies) == 2
    assert any(s.with_committee for s in cfg.strategies)
    assert any(not s.with_committee for s in cfg.strategies)


def test_ui_config_save_load_round_trip(data_root: Path):
    from dipdiver.ui.settings import (
        StrategyConfig,
        UiConfig,
        load_ui_config,
        save_ui_config,
    )
    new_cfg = UiConfig(
        strategies=[StrategyConfig(
            strategy_id="custom_strat", m1_config="custom.yaml",
            with_committee=True, enabled=False,
        )],
        timezone="America/New_York",
        telegram_chat_id="-100123",
    )
    save_ui_config(new_cfg)
    reloaded = load_ui_config()
    assert len(reloaded.strategies) == 1
    assert reloaded.strategies[0].strategy_id == "custom_strat"
    assert reloaded.strategies[0].enabled is False
    assert reloaded.timezone == "America/New_York"
    assert reloaded.telegram_chat_id == "-100123"


def test_reload_ui_config_picks_up_disk_changes(data_root: Path):
    """The /config page calls reload_ui_config after saving — must re-read."""
    from dipdiver._paths import ui_config_path
    from dipdiver.ui.settings import reload_ui_config, ui_config

    # First read primes the cache
    first = ui_config()
    assert first.last_modified_utc is None  # default

    # Manually mutate the YAML behind the cache
    import yaml
    ui_config_path().parent.mkdir(parents=True, exist_ok=True)
    ui_config_path().write_text(yaml.safe_dump({
        "strategies": [],
        "timezone": "Europe/Berlin",
        "last_modified_utc": "2026-06-04T00:00:00+00:00",
    }), encoding="utf-8")

    # Without reload, ui_config still returns the cached default
    assert ui_config().timezone == first.timezone

    # After reload, the change lands
    reloaded = reload_ui_config()
    assert reloaded.timezone == "Europe/Berlin"
    assert reloaded.strategies == []


def test_env_settings_picks_up_host_port(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DIPDIVER_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("DIPDIVER_UI_PORT", "9999")

    # Bust the lru_cache
    from dipdiver.ui.settings import env_settings
    env_settings.cache_clear()
    s = env_settings()
    assert s.host == "0.0.0.0"
    assert s.port == 9999


def test_env_settings_telegram_optional(monkeypatch: pytest.MonkeyPatch):
    """Unset telegram vars must yield None, not empty string."""
    monkeypatch.delenv("DIPDIVER_UI_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DIPDIVER_UI_TELEGRAM_CHAT_ID", raising=False)
    from dipdiver.ui.settings import env_settings
    env_settings.cache_clear()
    s = env_settings()
    assert s.telegram_bot_token is None
    assert s.telegram_chat_id is None


# ---------------------------------------------------------------------------
# db.py — table lifecycle
# ---------------------------------------------------------------------------


def test_init_db_creates_tables(data_root: Path):
    from dipdiver._paths import ui_db_path
    from dipdiver.ui import db

    db.init_db()
    assert ui_db_path().exists()

    # Open with raw sqlite and check tables.
    import sqlite3
    conn = sqlite3.connect(ui_db_path())
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert {"schedule_entries", "job_logs", "kill_switch_events", "config_audit"}.issubset(tables)


def test_init_db_idempotent(data_root: Path):
    """Calling init_db twice must not error or wipe existing rows."""
    from dipdiver.ui import db

    db.init_db()
    with db.session() as s:
        s.add(db.ScheduleEntry(
            job_id="probe", cron="0 0 * * *", enabled=True, description="probe",
        ))
    # Second call
    db.init_db()
    with db.session() as s:
        rows = s.query(db.ScheduleEntry).filter_by(job_id="probe").all()
    assert len(rows) == 1


def test_session_rolls_back_on_exception(data_root: Path):
    """Exceptions in the `with db.session()` block must not commit."""
    from dipdiver.ui import db
    db.init_db()
    try:
        with db.session() as s:
            s.add(db.ScheduleEntry(
                job_id="rollback_me", cron="0 0 * * *",
                enabled=True, description="x",
            ))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with db.session() as s:
        rows = s.query(db.ScheduleEntry).filter_by(job_id="rollback_me").all()
    assert rows == []
