"""Tests for the one-click market onboarding (/config → Add market).

The heavy stages (Yahoo fetch, Qlib training, signal export) are monkeypatched
at the market_onboard module level — run_onboard() resolves them via module
globals, so the pipeline wiring, gating, config mutation, and the background
job plumbing are all exercised for real against the per-test data root.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

GOOD_METRICS = {
    "sharpe": 1.2,
    "max_drawdown": 0.12,
    "hit_rate": 0.55,
    "annualised_return": 0.18,
}


def _patch_stages(monkeypatch, *, gate_passed=True, gate_reason="all gates passed"):
    from dipdiver.ui.jobs import market_onboard as mo

    monkeypatch.setattr(mo, "_missing_brain_deps", lambda mk: [])
    monkeypatch.setattr(
        mo, "_fetch_stage", lambda u, cfg, progress: "store already covers the test window"
    )
    monkeypatch.setattr(
        mo, "_train_stage", lambda uk, mk, progress: (GOOD_METRICS, gate_passed, gate_reason)
    )
    monkeypatch.setattr(mo, "_signals_stage", lambda uk, mk: 0)
    # load_config needs pyyaml only, but keep the test independent of the
    # baseline config loader (it asserts on qlib fields lazily anyway).
    import dipdiver.ui.routes.config_page  # noqa: F401 — ensure module import works

    class _FakeCfg:
        qlib_provider_uri = "data/qlib/us_data"
        test_end = "2025-12-31"
        train_start = "2014-01-01"
        train_end = "2022-12-31"
        test_start = "2024-01-01"

    import dipdiver.brain.baselines.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "load_config", lambda p: _FakeCfg())
    return mo


def _poll_until_done(client: TestClient, fragment: str, timeout: float = 15.0) -> str:
    m = re.search(r"/triggers/status/(\d+)", fragment)
    assert m, f"expected a polling fragment, got: {fragment}"
    url = m.group(0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(url).text
        if "auto-refreshing" not in body:
            return body
        time.sleep(0.05)
    raise AssertionError("onboard job did not finish in time")


def test_config_page_shows_add_market_form(client: TestClient, data_root: Path):
    r = client.get("/config")
    assert r.status_code == 200
    assert "Add market" in r.text
    assert "/config/markets/add" in r.text
    # registry-driven dropdown
    for key in ("dow30", "sp500", "nifty50"):
        assert key in r.text
    assert "research-only" in r.text


def test_add_market_unknown_universe_404(client: TestClient, data_root: Path):
    r = client.post("/config/markets/add", data={"universe": "ftse100", "model": "lightgbm"})
    assert r.status_code == 404


def test_add_market_unknown_model_400(client: TestClient, data_root: Path):
    r = client.post("/config/markets/add", data={"universe": "sp500", "model": "xgboost"})
    assert r.status_code == 400


def test_add_market_happy_path_enables_strategies(client: TestClient, data_root: Path, monkeypatch):
    _patch_stages(monkeypatch, gate_passed=True)

    r = client.post(
        "/config/markets/add",
        data={"universe": "sp500", "model": "lightgbm", "committee_variant": "on"},
    )
    assert r.status_code == 200
    assert "running" in r.text

    final = _poll_until_done(client, r.text)
    assert "rc=0" in final
    assert "pill-ok" in final
    assert "sp500 onboarded" in final

    from dipdiver.ui.settings import reload_ui_config

    cfg = reload_ui_config()
    sids = {s.strategy_id: s for s in cfg.strategies}
    assert "sp500_lightgbm" in sids
    assert "sp500_lightgbm_committee" in sids
    assert sids["sp500_lightgbm"].enabled
    assert sids["sp500_lightgbm"].with_committee is False
    assert sids["sp500_lightgbm_committee"].with_committee is True
    assert sids["sp500_lightgbm_committee"].m1_config == "sp500_lightgbm.yaml"

    # ModelVersion row recorded? _train_stage is stubbed, so no — but the
    # ConfigAudit trail must show the onboard.
    from dipdiver.ui import db

    with db.session() as s:
        audits = s.query(db.ConfigAudit).all()
    assert any("sp500_lightgbm" in a.diff_summary for a in audits)


def test_add_market_without_committee_variant(client: TestClient, data_root: Path, monkeypatch):
    _patch_stages(monkeypatch, gate_passed=True)
    r = client.post("/config/markets/add", data={"universe": "sp500", "model": "lightgbm"})
    final = _poll_until_done(client, r.text)
    assert "rc=0" in final

    from dipdiver.ui.settings import reload_ui_config

    sids = {s.strategy_id for s in reload_ui_config().strategies}
    assert "sp500_lightgbm" in sids
    assert "sp500_lightgbm_committee" not in sids


def test_add_market_gate_failure_does_not_enable(client: TestClient, data_root: Path, monkeypatch):
    _patch_stages(monkeypatch, gate_passed=False, gate_reason="sharpe 0.10 < 0.5")

    r = client.post(
        "/config/markets/add",
        data={"universe": "sp500", "model": "lightgbm", "committee_variant": "on"},
    )
    final = _poll_until_done(client, r.text)
    assert "rc=1" in final
    assert "NOT enabled" in final

    from dipdiver.ui.settings import reload_ui_config

    sids = {s.strategy_id for s in reload_ui_config().strategies}
    assert "sp500_lightgbm" not in sids


def test_add_market_missing_brain_deps_fails_cleanly(
    client: TestClient, data_root: Path, monkeypatch
):
    from dipdiver.ui.jobs import market_onboard as mo

    monkeypatch.setattr(mo, "_missing_brain_deps", lambda mk: ["qlib", "lightgbm"])
    r = client.post("/config/markets/add", data={"universe": "sp500", "model": "lightgbm"})
    final = _poll_until_done(client, r.text)
    assert "rc=1" in final
    assert "brain dependencies missing" in final


def test_add_market_busy_when_onboard_running(client: TestClient, data_root: Path):
    from dipdiver.ui.jobs import scheduler as sched_mod
    from dipdiver.ui.jobs.market_onboard import JOB_ID

    lock = sched_mod._lock_for(JOB_ID)
    assert lock.acquire(blocking=False)
    try:
        r = client.post("/config/markets/add", data={"universe": "sp500", "model": "lightgbm"})
    finally:
        lock.release()
    assert r.status_code == 200
    assert "busy" in r.text


def test_onboard_idempotent_on_existing_strategy(client: TestClient, data_root: Path, monkeypatch):
    """dow30 strategies already exist in the default config — onboarding dow30
    again must not duplicate them."""
    _patch_stages(monkeypatch, gate_passed=True)
    r = client.post(
        "/config/markets/add",
        data={"universe": "dow30", "model": "lightgbm", "committee_variant": "on"},
    )
    final = _poll_until_done(client, r.text)
    assert "rc=0" in final
    assert "already configured" in final

    from dipdiver.ui.settings import reload_ui_config

    cfg = reload_ui_config()
    assert len([s for s in cfg.strategies if s.strategy_id == "dow30_lightgbm"]) == 1
