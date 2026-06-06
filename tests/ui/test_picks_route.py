"""Smoke + integration tests for the /picks page and /watchlist endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _write_signal_csv(repo_root: Path, stem: str, rows: list[tuple[str, str, float]]):
    p = repo_root / "data" / "signals" / f"{stem}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,symbol,score"]
    for d, s, sc in rows:
        lines.append(f"{d},{s},{sc}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_picks_renders_empty_state_without_signal(client: TestClient, data_root: Path, monkeypatch):
    """No signal CSV → /picks shows the zero-state, not a 500."""
    # Override repo_root for the picks loader to land under data_root.
    import dipdiver._paths as paths_mod
    monkeypatch.setattr(paths_mod, "repo_root", lambda: data_root)
    import dipdiver.harness.picks as picks_mod
    monkeypatch.setattr(picks_mod, "repo_root", lambda: data_root)

    r = client.get("/picks?universe=dow30")
    assert r.status_code == 200
    body = r.text
    assert "No signal CSV found" in body


def test_picks_renders_top_picks_when_signal_exists(
    client: TestClient, data_root: Path, monkeypatch,
):
    import dipdiver._paths as paths_mod
    monkeypatch.setattr(paths_mod, "repo_root", lambda: data_root)
    import dipdiver.harness.picks as picks_mod
    monkeypatch.setattr(picks_mod, "repo_root", lambda: data_root)

    _write_signal_csv(data_root, "dow30_lightgbm", [
        ("2026-06-04", "AAPL", 0.12),
        ("2026-06-04", "AMZN", 0.08),
        ("2026-06-04", "MSFT", 0.05),
    ])
    r = client.get("/picks?universe=dow30")
    assert r.status_code == 200
    body = r.text
    assert "AAPL" in body
    assert "AMZN" in body
    assert "Tomorrow's picks" in body


@pytest.mark.parametrize("band", ["aggressive", "balanced", "conservative"])
def test_picks_accepts_all_risk_bands(client: TestClient, data_root: Path, monkeypatch, band):
    import dipdiver._paths as paths_mod
    monkeypatch.setattr(paths_mod, "repo_root", lambda: data_root)
    import dipdiver.harness.picks as picks_mod
    monkeypatch.setattr(picks_mod, "repo_root", lambda: data_root)
    _write_signal_csv(data_root, "dow30_lightgbm", [("2026-06-04", "AAPL", 0.1)])
    r = client.get(f"/picks?universe=dow30&risk={band}")
    assert r.status_code == 200


def test_watchlist_add_then_remove_round_trip(client: TestClient, data_root: Path):
    r = client.post(
        "/watchlist/add",
        data={"symbol": "GE", "universe": "dow30", "notes": "test"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    r2 = client.get("/watchlist?universe=dow30")
    assert "GE" in r2.text

    r3 = client.post(
        "/watchlist/remove",
        data={"symbol": "GE", "universe": "dow30"},
        follow_redirects=False,
    )
    assert r3.status_code == 303

    r4 = client.get("/watchlist?universe=dow30")
    assert "No symbols watched yet" in r4.text


def test_api_available_universes(client: TestClient):
    r = client.get("/api/available-universes")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_available_configs(client: TestClient, data_root: Path, monkeypatch):
    """Returns the YAML files found in the configs dir."""
    # Existing configs dir lives at repo_root/dipdiver/brain/baselines/configs
    r = client.get("/api/available-configs")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    # The known dow30_lightgbm.yaml should be in the live repo dir
    filenames = {d["filename"] for d in data}
    assert "dow30_lightgbm.yaml" in filenames


def test_decisions_note_round_trip(
    client: TestClient, data_root: Path, seeded_run_record, patch_repo_root_to_data_root,
):
    r = client.post(
        "/decisions/2026-06-04/CVX/note",
        data={"universe": "dow30", "note": "watch macro release Thursday"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r2 = client.get("/decisions/2026-06-04/CVX?universe=dow30")
    assert "watch macro release Thursday" in r2.text


def test_decisions_feedback_round_trip(
    client: TestClient, data_root: Path, seeded_run_record, patch_repo_root_to_data_root,
):
    r = client.post(
        "/decisions/2026-06-04/CVX/feedback",
        data={"universe": "dow30", "rating": "-1", "notes": "earnings risk"},
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_decisions_override_requires_reason(
    client: TestClient, data_root: Path, seeded_run_record, patch_repo_root_to_data_root,
):
    """Override without a reason must be rejected."""
    r = client.post(
        "/decisions/2026-06-04/CVX/override",
        data={"universe": "dow30", "new_decision": "vetoed", "reason": ""},
    )
    assert r.status_code == 422


def test_decisions_override_with_reason_succeeds(
    client: TestClient, data_root: Path, seeded_run_record, patch_repo_root_to_data_root,
):
    r = client.post(
        "/decisions/2026-06-04/CVX/override",
        data={"universe": "dow30", "new_decision": "vetoed",
              "reason": "macro concern"},
        follow_redirects=False,
    )
    assert r.status_code == 303
