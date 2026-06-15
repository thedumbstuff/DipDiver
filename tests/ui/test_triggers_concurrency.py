"""Concurrency tests for /triggers — second click while running must NOT
queue a duplicate run.

We test the contract at the lock + route layer (deterministic) rather than
trying to race two real HTTP requests through TestClient (sync ASGI transport
serialises them, making the race impossible to reproduce in-process).
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_trigger_now_returns_busy_dict_when_already_running(
    client: TestClient,
    data_root: Path,
    monkeypatch,
):
    """Unit-level: trigger_now() returns rc=409 + busy=True when locked."""
    from dipdiver.ui.jobs import scheduler as sched_mod

    # Manually acquire the lock to simulate a run in progress.
    lock = sched_mod._lock_for("pnl_settle")
    acquired = lock.acquire(blocking=False)
    assert acquired
    try:
        result = sched_mod.trigger_now("pnl_settle")
    finally:
        lock.release()
    assert result["rc"] == 409
    assert result["busy"] is True
    assert "already running" in result["error"].lower()


def test_is_job_running_reflects_lock_state():
    """The /triggers UI uses this helper to grey out running buttons."""
    from dipdiver.ui.jobs import scheduler as sched_mod

    assert sched_mod.is_job_running("test_job") is False
    lock = sched_mod._lock_for("test_job")
    lock.acquire()
    try:
        assert sched_mod.is_job_running("test_job") is True
    finally:
        lock.release()
    assert sched_mod.is_job_running("test_job") is False


def test_triggers_page_button_has_disabled_elt_attr(client: TestClient):
    """Belt-and-suspenders: the template must include hx-disabled-elt."""
    r = client.get("/triggers")
    assert r.status_code == 200
    body = r.text
    # The attribute that HTMX uses to disable the button mid-request.
    assert 'hx-disabled-elt="find button"' in body
    # And the JS fallback that flips aria-disabled.
    assert "htmx:beforeRequest" in body
    assert "Running…" in body


def test_trigger_run_returns_immediately_and_polls_to_completion(
    client: TestClient,
    data_root: Path,
    monkeypatch,
):
    """POST /triggers/run must NOT block on the job: it returns a polling
    fragment at once, and /triggers/status/{id} flips to rc=0 when done."""
    import re
    import threading
    import time

    from dipdiver.ui.jobs import scheduler as sched_mod
    from dipdiver.ui.jobs.registry import JobDef

    release = threading.Event()

    def slow_job() -> dict:
        release.wait(timeout=5)
        return {"rc": 0, "message": "slow job done"}

    fake = JobDef(
        job_id="pnl_settle",
        description="stub",
        default_cron="0 0 * * *",
        func=slow_job,
    )
    monkeypatch.setattr(sched_mod, "get_job", lambda jid: fake)

    t0 = time.monotonic()
    r = client.post("/triggers/run", data={"job_id": "pnl_settle"})
    elapsed = time.monotonic() - t0
    assert r.status_code == 200
    assert "running" in r.text
    assert elapsed < 2, "POST must return before the job finishes"

    m = re.search(r"/triggers/status/(\d+)", r.text)
    assert m, "fragment must point at the status endpoint"
    log_id = int(m.group(1))

    # Still running → status returns another polling fragment.
    r2 = client.get(f"/triggers/status/{log_id}")
    assert "running" in r2.text

    # Let the job finish and poll until the row flips.
    release.set()
    deadline = time.monotonic() + 5
    final = ""
    while time.monotonic() < deadline:
        final = client.get(f"/triggers/status/{log_id}").text
        if "running" not in final:
            break
        time.sleep(0.05)
    assert "rc=0" in final
    assert "pill-ok" in final
    assert "slow job done" in final


def test_busy_fragment_renders_warn_pill(client: TestClient, data_root: Path):
    """Direct check: when trigger_now returns busy, the route returns a pill-warn fragment."""
    from dipdiver.ui.jobs import scheduler as sched_mod

    lock = sched_mod._lock_for("pnl_settle")
    lock.acquire()
    try:
        r = client.post("/triggers/run", data={"job_id": "pnl_settle"})
    finally:
        lock.release()
    assert r.status_code == 200
    assert "pill-warn" in r.text
    assert "busy" in r.text
