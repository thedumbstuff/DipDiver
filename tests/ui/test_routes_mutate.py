"""POST routes that change state: config save, schedule save, trigger run.

These tests assert the full round-trip — request → DB row written →
side-effect visible on subsequent GET.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# /config/save
# ---------------------------------------------------------------------------


def test_config_save_persists_yaml_and_redirects(client: TestClient, data_root: Path):
    payload = {
        "strategy_id": ["dow30_lightgbm", "dow30_lightgbm_committee"],
        "m1_config":   ["dow30_lightgbm.yaml", "dow30_lightgbm.yaml"],
        "enabled":     ["e_0", "e_1"],
        "with_committee": ["c_1"],  # only second one
        "timezone": "UTC",
        "telegram_chat_id": "",
    }
    r = client.post("/config/save", data=payload, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/config?saved=ok"

    # YAML must exist + reflect the form values.
    from dipdiver._paths import ui_config_path
    assert ui_config_path().exists()
    import yaml
    raw = yaml.safe_load(ui_config_path().read_text(encoding="utf-8"))
    assert len(raw["strategies"]) == 2
    assert raw["strategies"][0]["with_committee"] is False
    assert raw["strategies"][1]["with_committee"] is True


def test_config_save_writes_audit_row(client: TestClient, data_root: Path):
    client.post("/config/save", data={
        "strategy_id": "test_strategy",
        "m1_config": "x.yaml",
        "enabled": "e_0",
        "timezone": "UTC",
    }, follow_redirects=False)
    from dipdiver.ui import db
    with db.session() as s:
        rows = s.query(db.ConfigAudit).all()
    assert len(rows) >= 1
    assert rows[-1].actor == "operator"


def test_config_save_then_get_shows_saved_flash(client: TestClient, data_root: Path):
    client.post("/config/save", data={
        "strategy_id": "foo",
        "m1_config": "foo.yaml",
        "enabled": "e_0",
        "timezone": "UTC",
    }, follow_redirects=False)
    r = client.get("/config?saved=ok")
    assert "Saved." in r.text
    assert "foo" in r.text  # strategy id round-tripped


def test_config_save_drops_blank_strategy_ids(client: TestClient, data_root: Path):
    """Empty strategy_id row (the always-shown blank row) is silently dropped."""
    client.post("/config/save", data={
        "strategy_id": ["real_strat", ""],
        "m1_config":   ["a.yaml", "b.yaml"],
        "enabled":     ["e_0"],
        "timezone": "UTC",
    }, follow_redirects=False)
    import yaml
    from dipdiver._paths import ui_config_path
    raw = yaml.safe_load(ui_config_path().read_text(encoding="utf-8"))
    assert len(raw["strategies"]) == 1
    assert raw["strategies"][0]["strategy_id"] == "real_strat"


# ---------------------------------------------------------------------------
# /schedule/save
# ---------------------------------------------------------------------------


def test_schedule_save_updates_cron(client: TestClient, data_root: Path):
    # nightly_run was inserted at boot with default cron.
    r = client.post("/schedule/save", data={
        "job_id": "nightly_run",
        "cron":   "0 13 * * 1-5",  # new cron
        "enabled": "on",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/schedule?saved=ok"

    from dipdiver.ui import db
    with db.session() as s:
        row = s.query(db.ScheduleEntry).filter_by(job_id="nightly_run").one()
    assert row.cron == "0 13 * * 1-5"
    assert row.enabled is True


def test_schedule_save_disable_then_reenable(client: TestClient, data_root: Path):
    client.post("/schedule/save", data={
        "job_id": "nightly_run",
        "cron":   "35 14 * * 1-5",
        # enabled omitted → False
    }, follow_redirects=False)
    from dipdiver.ui import db
    with db.session() as s:
        assert s.query(db.ScheduleEntry).filter_by(job_id="nightly_run").one().enabled is False

    client.post("/schedule/save", data={
        "job_id": "nightly_run",
        "cron":   "35 14 * * 1-5",
        "enabled": "on",
    }, follow_redirects=False)
    with db.session() as s:
        assert s.query(db.ScheduleEntry).filter_by(job_id="nightly_run").one().enabled is True


def test_schedule_save_invalid_cron_rejected(client: TestClient, data_root: Path):
    """Garbage cron must surface an error redirect, not 500."""
    r = client.post("/schedule/save", data={
        "job_id": "nightly_run",
        "cron":   "this is not cron",
        "enabled": "on",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/schedule?saved=error:")


# ---------------------------------------------------------------------------
# /triggers/run
# ---------------------------------------------------------------------------


def test_trigger_unknown_job_404(client: TestClient, data_root: Path):
    r = client.post("/triggers/run", data={"job_id": "does_not_exist"})
    assert r.status_code == 404


def test_trigger_pnl_settle_returns_stub_ok(client: TestClient, data_root: Path):
    """The pnl_settle job is a stub — should return rc=0 cleanly."""
    r = client.post("/triggers/run", data={"job_id": "pnl_settle"})
    assert r.status_code == 200
    body = r.text
    assert "pill-ok" in body
    assert "rc=0" in body


def test_trigger_writes_joblog_row(client: TestClient, data_root: Path):
    client.post("/triggers/run", data={"job_id": "pnl_settle"})
    from dipdiver.ui import db
    with db.session() as s:
        logs = s.query(db.JobLog).filter_by(job_id="pnl_settle").all()
    assert len(logs) == 1
    log = logs[0]
    assert log.status == "success"
    assert log.exit_code == 0
    assert log.triggered_by == "manual"
    assert log.started_utc is not None
    assert log.finished_utc is not None


def test_trigger_scoreboard_render_writes_file(client: TestClient, data_root: Path, seeded_scoreboard):
    r = client.post("/triggers/run", data={"job_id": "scoreboard_render"})
    assert r.status_code == 200
    from dipdiver._paths import ui_rendered_dir
    rendered = ui_rendered_dir() / "SCOREBOARD.md"
    assert rendered.exists()
    text = rendered.read_text(encoding="utf-8")
    assert "DipDiver Scoreboard" in text
    assert "dow30_lightgbm" in text


def test_failing_trigger_marks_joblog_error(client: TestClient, data_root: Path, monkeypatch: pytest.MonkeyPatch):
    """If the inner job raises, the JobLog row should reflect error + carry the message."""
    import dipdiver.ui.jobs.pnl_settle as pnl_mod

    def boom() -> dict:
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(pnl_mod, "run", boom)
    r = client.post("/triggers/run", data={"job_id": "pnl_settle"})
    assert r.status_code == 200
    assert "pill-err" in r.text or "rc=1" in r.text

    from dipdiver.ui import db
    with db.session() as s:
        logs = s.query(db.JobLog).filter_by(job_id="pnl_settle").all()
    assert logs[-1].status == "error"
    assert "simulated failure" in (logs[-1].error or "")
