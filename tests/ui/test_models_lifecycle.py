"""Tests for the M10 model lifecycle: ModelVersion table + /models route."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _add_model_version(
    *, config_name: str, test_end: str,
    status: str = "locked", sharpe: float = 1.2,
) -> None:
    from dipdiver.ui import db
    with db.session() as s:
        s.add(db.ModelVersion(
            config_name=config_name,
            config_hash="abc123",
            locked_on_utc=datetime.now(timezone.utc),
            train_start="2020-01-01", train_end="2023-12-31",
            test_start="2025-01-01", test_end=test_end,
            sharpe=sharpe, max_dd=0.08, hit_rate=0.52,
            status=status, notes="",
        ))


def test_models_page_empty_renders_zero_state(client: TestClient, data_root: Path):
    r = client.get("/models")
    assert r.status_code == 200
    assert "No model versions recorded yet" in r.text


def test_models_page_lists_seeded_rows(client: TestClient, data_root: Path):
    # Init DB through the client
    client.get("/models")
    _add_model_version(config_name="dow30_lightgbm.yaml", test_end="2027-12-31")
    r = client.get("/models")
    assert "dow30_lightgbm.yaml" in r.text


def test_age_badge_red_when_expired(client: TestClient, data_root: Path):
    client.get("/models")
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    _add_model_version(config_name="dow30_lightgbm.yaml", test_end=yesterday)
    from dipdiver.ui.routes.models_page import model_age_badge
    badge = model_age_badge(yesterday)
    assert badge["tone"] == "err"


def test_age_badge_yellow_within_30d(client: TestClient, data_root: Path):
    from dipdiver.ui.routes.models_page import model_age_badge
    soon = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    badge = model_age_badge(soon)
    assert badge["tone"] == "warn"


def test_age_badge_green_when_far_out(client: TestClient, data_root: Path):
    from dipdiver.ui.routes.models_page import model_age_badge
    far = (datetime.now(timezone.utc).date() + timedelta(days=300)).isoformat()
    badge = model_age_badge(far)
    assert badge["tone"] == "ok"


def test_latest_locked_version_returns_newest(client: TestClient, data_root: Path):
    client.get("/models")
    from dipdiver.ui.routes.models_page import latest_locked_version
    from dipdiver.ui import db
    with db.session() as s:
        s.add(db.ModelVersion(
            config_name="dow30_lightgbm.yaml",
            config_hash="old",
            locked_on_utc=datetime.now(timezone.utc) - timedelta(days=30),
            train_start="2020-01-01", train_end="2022-12-31",
            test_start="2024-01-01", test_end="2024-12-31",
            sharpe=0.5, max_dd=0.05, hit_rate=0.5,
            status="locked",
        ))
        s.add(db.ModelVersion(
            config_name="dow30_lightgbm.yaml",
            config_hash="new",
            locked_on_utc=datetime.now(timezone.utc),
            train_start="2021-01-01", train_end="2023-12-31",
            test_start="2025-01-01", test_end="2025-12-31",
            sharpe=1.2, max_dd=0.06, hit_rate=0.55,
            status="locked",
        ))
    out = latest_locked_version("dow30_lightgbm.yaml")
    assert out is not None
    assert out["config_hash"] == "new"
    assert out["sharpe"] == 1.2


def test_m1_retrain_records_rejected_when_qlib_unavailable(
    client: TestClient, data_root: Path, monkeypatch,
):
    """When qlib is genuinely missing (ModuleNotFoundError) → 'module_missing'.

    Distinct from import_error (typo in our code) — that one used to be
    misclassified as qlib_unavailable, hiding bugs.
    """
    from dipdiver.ui.jobs import m1_retrain
    import dipdiver.brain.baselines.config as cfg_mod
    from dipdiver.brain.baselines.config import BaselineConfig

    def fake_load(p):
        return BaselineConfig(
            name="x", universe="dow30", model="lightgbm",
            train_start="2020-01-01", train_end="2022-12-31",
            valid_start="2023-01-01", valid_end="2023-12-31",
            test_start="2024-01-01", test_end="2024-12-31",
            benchmark="SPY", qlib_provider_uri="x", region="us", seed=42,
        )

    monkeypatch.setattr(m1_retrain, "_resolve_config_path",
                        lambda fn: data_root / "dow30_lightgbm.yaml")
    (data_root / "dow30_lightgbm.yaml").write_text("placeholder", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "load_config", fake_load)

    # Force the runner import to raise ModuleNotFoundError("qlib")
    import builtins
    original_import = builtins.__import__

    def patched_import(name, *args, **kwargs):
        if name == "dipdiver.brain.baselines.runner":
            raise ModuleNotFoundError("No module named 'qlib'", name="qlib")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", patched_import)

    result = m1_retrain.run()
    assert result["rc"] in (0, 1)
    from dipdiver.ui import db
    with db.session() as s:
        rows = s.query(db.ModelVersion).all()
    rejected = [r for r in rows if r.status == "rejected"]
    assert rejected, "expected at least one rejected row"
    assert "module_missing" in rejected[-1].notes


def test_m1_retrain_surfaces_real_import_error_not_qlib_unavailable(
    client: TestClient, data_root: Path, monkeypatch,
):
    """Regression: an ImportError raised by a typo in our code must NOT be
    misclassified as qlib_unavailable. The previous version of this job
    caught every ImportError and reported 'qlib_unavailable', hiding the
    actual bug ('cannot import name dump_result').
    """
    from dipdiver.ui.jobs import m1_retrain
    import dipdiver.brain.baselines.config as cfg_mod
    from dipdiver.brain.baselines.config import BaselineConfig

    def fake_load(p):
        return BaselineConfig(
            name="x", universe="dow30", model="lightgbm",
            train_start="2020-01-01", train_end="2022-12-31",
            valid_start="2023-01-01", valid_end="2023-12-31",
            test_start="2024-01-01", test_end="2024-12-31",
            benchmark="SPY", qlib_provider_uri="x", region="us", seed=42,
        )

    monkeypatch.setattr(m1_retrain, "_resolve_config_path",
                        lambda fn: data_root / "dow30_lightgbm.yaml")
    (data_root / "dow30_lightgbm.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "load_config", fake_load)

    # Force an ImportError that's NOT a missing module
    import builtins
    original_import = builtins.__import__

    def patched_import(name, *args, **kwargs):
        if name == "dipdiver.brain.baselines.runner":
            raise ImportError("cannot import name 'nonexistent_thing'")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", patched_import)

    result = m1_retrain.run()
    from dipdiver.ui import db
    with db.session() as s:
        rejected = [r for r in s.query(db.ModelVersion).all() if r.status == "rejected"]
    assert rejected
    notes = rejected[-1].notes
    # Must surface the REAL error, not a generic "qlib_unavailable"
    assert "import_error" in notes
    assert "ImportError" in notes
    assert "qlib_unavailable" not in notes


def test_m1_retrain_extracts_metrics_from_BaselineResult_dataclass(
    client: TestClient, data_root: Path, monkeypatch,
):
    """Regression: run_baseline returns a frozen dataclass, NOT a dict. The
    job must read attributes (outcome.sharpe), not call .get() or .metrics.
    """
    from dipdiver.ui.jobs import m1_retrain
    import dipdiver.brain.baselines.config as cfg_mod
    from dipdiver.brain.baselines.config import BaselineConfig
    from dipdiver.brain.baselines.results import BaselineResult

    def fake_load(p):
        return BaselineConfig(
            name="x", universe="dow30", model="lightgbm",
            train_start="2020-01-01", train_end="2022-12-31",
            valid_start="2023-01-01", valid_end="2023-12-31",
            test_start="2024-01-01", test_end="2024-12-31",
            benchmark="SPY", qlib_provider_uri="x", region="us", seed=42,
        )

    fake_result = BaselineResult(
        config_hash="abc", config_name="x", universe="dow30", model="lightgbm",
        test_start="2024-01-01", test_end="2024-12-31",
        annualised_return=0.18, annualised_volatility=0.15,
        sharpe=1.2, max_drawdown=-0.08, hit_rate=0.55,
        turnover=2.0, n_trades=42,
        benchmark_annualised_return=0.10, excess_return=0.08,
        qlib_version="0.9.7", git_sha="deadbeef", run_timestamp_utc="2026-06-06T00:00:00Z",
        psr=0.99,  # clears the PSR>=0.95 gate so this run locks (see _LOCK_GATES)
    )

    monkeypatch.setattr(m1_retrain, "_resolve_config_path",
                        lambda fn: data_root / "dow30_lightgbm.yaml")
    (data_root / "dow30_lightgbm.yaml").write_text("x", encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "load_config", fake_load)

    # Stub the runner so the test doesn't actually call qlib
    import dipdiver.brain.baselines.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_baseline", lambda cfg: fake_result)

    result = m1_retrain.run()
    assert result["rc"] == 0
    from dipdiver.ui import db
    with db.session() as s:
        rows = sorted(s.query(db.ModelVersion).all(), key=lambda r: r.id)
    # Sharpe=1.2 > 0.5, MDD=0.08 < 0.30, hit=0.55 > 0.45, PSR=0.99 > 0.95 → LOCKED
    assert rows[-1].status == "locked"
    assert rows[-1].sharpe == pytest.approx(1.2)
    assert rows[-1].hit_rate == pytest.approx(0.55)
