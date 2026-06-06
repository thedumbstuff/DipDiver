"""Kill-switch handler and security boundary tests.

Kill-switch tests use the mock_alpaca fixture so we never touch a real broker.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


def test_kill_switch_requires_FLATTEN_confirmation(
    client: TestClient, data_root: Path, mock_alpaca,
):
    r = client.post("/health/kill-switch", data={
        "reason": "panic",
        "confirm": "wrong-word",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/health?error=must-type-FLATTEN"
    # No broker calls should have happened.
    mock_alpaca.client.cancel_orders.assert_not_called()
    mock_alpaca.client.close_all_positions.assert_not_called()


def test_kill_switch_fires_broker_actions_when_confirmed(
    client: TestClient, data_root: Path, mock_alpaca,
):
    r = client.post("/health/kill-switch", data={
        "reason": "test run",
        "confirm": "FLATTEN",
    }, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith("/health?kill=")
    mock_alpaca.client.cancel_orders.assert_called_once()
    mock_alpaca.client.close_all_positions.assert_called_once()


def test_kill_switch_disables_nightly_job(
    client: TestClient, data_root: Path, mock_alpaca,
):
    client.post("/health/kill-switch", data={
        "reason": "test", "confirm": "FLATTEN",
    }, follow_redirects=False)
    from dipdiver.ui import db
    with db.session() as s:
        row = s.query(db.ScheduleEntry).filter_by(job_id="nightly_run").one()
    assert row.enabled is False


def test_kill_switch_writes_audit_row(
    client: TestClient, data_root: Path, mock_alpaca,
):
    client.post("/health/kill-switch", data={
        "reason": "audit-test reason",
        "confirm": "FLATTEN",
    }, follow_redirects=False)
    from dipdiver.ui import db
    with db.session() as s:
        events = s.query(db.KillSwitchEvent).all()
    assert len(events) == 1
    ev = events[0]
    assert "audit-test reason" in ev.reason
    assert "cancelled all open orders" in ev.actions_taken
    assert ev.actor == "operator"


def test_kill_switch_partial_when_broker_fails(
    client: TestClient, data_root: Path, mock_alpaca,
):
    """If close_all_positions raises, status is 'partial', not 'succeeded'."""
    mock_alpaca.client.close_all_positions.side_effect = RuntimeError("broker down")
    client.post("/health/kill-switch", data={
        "reason": "test", "confirm": "FLATTEN",
    }, follow_redirects=False)
    from dipdiver.ui import db
    with db.session() as s:
        ev = s.query(db.KillSwitchEvent).order_by(db.KillSwitchEvent.id.desc()).first()
    assert ev is not None
    assert ev.status == "partial"
    assert "close_all_positions FAILED" in ev.actions_taken


# ---------------------------------------------------------------------------
# Path traversal on /logs/{path}
# ---------------------------------------------------------------------------


def test_logs_tail_rejects_path_traversal(client: TestClient, data_root: Path):
    """`..` segments must not escape the logs directory."""
    # Drop a sentinel file outside the logs dir
    secret = data_root / "secrets.txt"
    secret.write_text("hidden", encoding="utf-8")

    # Various path-traversal attempts
    for trick in (
        "../secrets.txt",
        "..%2Fsecrets.txt",
        "../../etc/passwd",
    ):
        r = client.get(f"/logs/{trick}")
        # Either 403 (forbidden) or 404 (not found) is acceptable;
        # a 200 with the secret would mean traversal succeeded.
        assert r.status_code in (403, 404), f"trick {trick} returned {r.status_code}"
        assert "hidden" not in r.text


def test_logs_tail_404_for_missing_file(client: TestClient, data_root: Path):
    # ensure logs dir exists so the route doesn't 500 before reaching the missing-file branch
    (data_root / "logs").mkdir(parents=True, exist_ok=True)
    r = client.get("/logs/never-exists.log")
    assert r.status_code == 404


def test_logs_tail_returns_last_lines(client: TestClient, data_root: Path):
    logs_dir = data_root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    f = logs_dir / "demo.log"
    f.write_text("\n".join(f"line {i}" for i in range(1, 51)), encoding="utf-8")

    r = client.get("/logs/demo.log?lines=5")
    assert r.status_code == 200
    body = r.text
    assert "line 50" in body
    assert "line 46" in body
    assert "line 1" not in body  # truncated


# ---------------------------------------------------------------------------
# Health page surfaces Alpaca state
# ---------------------------------------------------------------------------


def test_health_renders_alpaca_disconnected_when_no_client(
    client: TestClient, data_root: Path, monkeypatch: pytest.MonkeyPatch,
):
    """When AlpacaPaperClient raises (no creds), /health renders the disconnected card."""
    import dipdiver.adapters.alpaca.client as alpaca_mod

    def boom(*a, **k):
        raise RuntimeError("ALPACA_API_KEY not set")

    monkeypatch.setattr(alpaca_mod, "AlpacaPaperClient", boom)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.text
    assert "disconnected" in body
    assert "ALPACA_API_KEY" in body
