"""APScheduler boot + DB-driven job registration.

On startup:
  1. Make sure every JobDef has a ScheduleEntry row (insert default cron if not).
  2. Read ScheduleEntry rows and register enabled jobs with APScheduler.
  3. Wrap each invocation in a JobLog writer + Telegram alert on failure.

Editing a schedule via the UI calls update_schedule() which updates the DB
row and re-registers the APScheduler job.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from croniter import croniter

from dipdiver.ui import db
from dipdiver.ui.jobs.alerts import send_alert
from dipdiver.ui.jobs.registry import JobDef, all_jobs, get_job

log = logging.getLogger(__name__)


_scheduler: BackgroundScheduler | None = None


# Per-job concurrency guard for manual /triggers invocations.
# APScheduler enforces max_instances=1 at the SCHEDULED level, but
# `trigger_now()` calls the wrapped job directly. Without this lock, two
# parallel POSTs to /triggers/run would both kick off the same job.
class JobBusyError(RuntimeError):
    """Raised when a manual trigger arrives while the same job is already running."""

    def __init__(self, job_id: str) -> None:
        super().__init__(f"job {job_id!r} is already running")
        self.job_id = job_id


_job_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(job_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _job_locks.get(job_id)
        if lock is None:
            lock = threading.Lock()
            _job_locks[job_id] = lock
        return lock


def is_job_running(job_id: str) -> bool:
    """Best-effort: does the per-job lock indicate a run in progress right now?"""
    lock = _job_locks.get(job_id)
    if lock is None:
        return False
    if lock.acquire(blocking=False):
        lock.release()
        return False
    return True


def currently_running_jobs() -> set[str]:
    """Snapshot of job_ids holding their per-job lock."""
    with _locks_guard:
        ids = list(_job_locks.keys())
    return {jid for jid in ids if is_job_running(jid)}


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(timezone="UTC")
    return _scheduler


def _ensure_schedule_entries() -> None:
    """Insert default cron rows for any JobDef not yet in the DB. Idempotent."""
    with db.session() as s:
        existing = {row.job_id for row in s.query(db.ScheduleEntry).all()}
        for jd in all_jobs():
            if jd.job_id not in existing:
                s.add(
                    db.ScheduleEntry(
                        job_id=jd.job_id,
                        cron=jd.default_cron,
                        enabled=True,
                        description=jd.description,
                    )
                )


def _wrap_job(jd: JobDef, triggered_by: str = "scheduler"):
    """Return a callable that runs the job + writes a JobLog row + alerts on fail.

    Honours the per-job lock so concurrent triggers serialize. The lock is
    acquired non-blocking from `trigger_now()` (so the UI gets an immediate
    "already running" response), but blocking from the scheduler path so a
    cron fire never silently drops.
    """

    def wrapped(*, blocking: bool = True) -> dict | None:
        lock = _lock_for(jd.job_id)
        acquired = lock.acquire(blocking=blocking)
        if not acquired:
            raise JobBusyError(jd.job_id)
        try:
            return _execute_locked(jd, triggered_by)
        finally:
            lock.release()

    return wrapped


def _create_log_row(jd: JobDef, triggered_by: str) -> int:
    """Insert the `running` JobLog row and return its id."""
    with db.session() as s:
        row = db.JobLog(
            job_id=jd.job_id,
            started_utc=datetime.now(UTC),
            status="running",
            triggered_by=triggered_by,
        )
        s.add(row)
        s.flush()
        return row.id


def _execute_locked(jd: JobDef, triggered_by: str, log_id: int | None = None) -> dict:
    if log_id is None:
        log_id = _create_log_row(jd, triggered_by)
    try:
        result = jd.func() or {}
        rc = result.get("rc", 0)
        summary = result.get("message") or _short_summary(result)
        status = "success" if rc == 0 else "error"
        # Keep the job's own explanation — "rc=1" alone is useless in the UI
        # fragment and in the Telegram alert.
        error = None if rc == 0 else (result.get("error") or result.get("message") or f"rc={rc}")
    except Exception as e:
        log.exception("job %s crashed", jd.job_id)
        result = {"rc": 1}
        rc = 1
        status = "error"
        summary = ""
        error = f"{type(e).__name__}: {e}"
    finished = datetime.now(UTC)
    with db.session() as s:
        row = s.get(db.JobLog, log_id)
        if row is not None:
            row.finished_utc = finished
            row.status = status
            row.exit_code = rc
            row.summary = summary
            row.error = error
    if status == "error":
        send_alert(
            f"job `{jd.job_id}` failed: {error or 'unknown'}",
            severity="error",
        )
    return result


def _short_summary(result: dict) -> str:
    """Compact one-liner for the JobLog row."""
    if not isinstance(result, dict):
        return str(result)[:200]
    parts = []
    for k, v in result.items():
        if k in ("results", "rc"):
            continue
        parts.append(f"{k}={v}")
    return ", ".join(parts)[:240]


def register_all() -> None:
    """Boot the scheduler. Call once at app startup."""
    db.init_db()
    _ensure_schedule_entries()
    sched = get_scheduler()
    sched.remove_all_jobs()

    with db.session() as s:
        rows = s.query(db.ScheduleEntry).all()
        entries = [(r.job_id, r.cron, r.enabled) for r in rows]

    for job_id, cron, enabled in entries:
        if not enabled:
            continue
        jd = get_job(job_id)
        if jd is None:
            log.warning("schedule row for unknown job %s — skipping", job_id)
            continue
        try:
            trigger = CronTrigger.from_crontab(cron, timezone="UTC")
        except Exception as e:
            log.error("invalid cron for %s (%r): %s", job_id, cron, e)
            continue
        sched.add_job(_wrap_job(jd), trigger, id=job_id, name=jd.description, max_instances=1)
        log.info("scheduled %s (%s)", job_id, cron)

    if not sched.running:
        sched.start()


def update_schedule(job_id: str, cron: str, enabled: bool) -> None:
    """Persist + re-register one schedule. Called from /schedule POST."""
    croniter(cron)  # raises if invalid
    with db.session() as s:
        row = s.query(db.ScheduleEntry).filter_by(job_id=job_id).one()
        row.cron = cron
        row.enabled = enabled
        row.last_modified_utc = datetime.now(UTC)
    sched = get_scheduler()
    with contextlib.suppress(Exception):
        sched.remove_job(job_id)
    jd = get_job(job_id)
    if jd and enabled:
        trigger = CronTrigger.from_crontab(cron, timezone="UTC")
        sched.add_job(_wrap_job(jd), trigger, id=job_id, name=jd.description, max_instances=1)


def trigger_now(job_id: str, triggered_by: str = "manual") -> dict:
    """Run a job synchronously, bypassing the schedule. Used by /triggers.

    Concurrency contract: if the same job is already running (manual or
    scheduled), this returns rc=409 with a busy message — does NOT block
    waiting. The HTTP layer surfaces this as a 409 to keep the UI snappy.
    """
    jd = get_job(job_id)
    if jd is None:
        return {"rc": 1, "error": f"unknown job_id {job_id}"}
    try:
        return _wrap_job(jd, triggered_by=triggered_by)(blocking=False) or {}
    except JobBusyError:
        return {
            "rc": 409,
            "error": f"job {job_id!r} is already running. Wait for it to finish.",
            "busy": True,
        }


def trigger_async(job_id: str, triggered_by: str = "manual") -> dict:
    """Start a job in a background thread and return immediately.

    Used by /triggers so a long job never blocks the event loop (and thus the
    whole UI). Returns {"log_id": …, "started": True} on success; the caller
    polls the JobLog row (via /triggers/status/{log_id}) for completion.

    Same busy contract as trigger_now(): if the per-job lock is held, returns
    rc=409 + busy=True without queueing a second run. The lock is acquired
    HERE (request thread) and released by the worker thread, so two parallel
    POSTs can never both spawn a run.
    """
    jd = get_job(job_id)
    if jd is None:
        return {"rc": 1, "error": f"unknown job_id {job_id}"}
    lock = _lock_for(job_id)
    if not lock.acquire(blocking=False):
        return {
            "rc": 409,
            "error": f"job {job_id!r} is already running. Wait for it to finish.",
            "busy": True,
        }
    try:
        log_id = _create_log_row(jd, triggered_by)
    except Exception:
        lock.release()
        raise

    def _run() -> None:
        try:
            _execute_locked(jd, triggered_by, log_id=log_id)
        finally:
            lock.release()

    threading.Thread(target=_run, name=f"trigger-{job_id}", daemon=True).start()
    return {"rc": 0, "log_id": log_id, "started": True}


def run_adhoc(
    job_id: str,
    func: Callable[[Callable[[str], None]], dict],
    *,
    description: str = "",
    triggered_by: str = "manual",
) -> dict:
    """Run a one-off parameterised callable in a background thread.

    Like trigger_async(), but for work that is not in the cron registry (e.g.
    market onboarding, which takes arguments). `func` receives a
    `progress(msg)` callback that live-updates the JobLog row's summary, so
    the /triggers/status polling fragment shows stage-by-stage progress.

    Same busy contract: one run per job_id at a time, rc=409 + busy=True when
    the lock is already held.
    """
    lock = _lock_for(job_id)
    if not lock.acquire(blocking=False):
        return {
            "rc": 409,
            "error": f"job {job_id!r} is already running. Wait for it to finish.",
            "busy": True,
        }
    placeholder = JobDef(job_id=job_id, description=description, default_cron="", func=dict)
    try:
        log_id = _create_log_row(placeholder, triggered_by)
    except Exception:
        lock.release()
        raise

    def _progress(msg: str) -> None:
        with db.session() as s:
            row = s.get(db.JobLog, log_id)
            if row is not None and row.status == "running":
                row.summary = msg[:240]

    bound = JobDef(
        job_id=job_id,
        description=description,
        default_cron="",
        func=lambda: func(_progress),
    )

    def _run() -> None:
        try:
            _execute_locked(bound, triggered_by, log_id=log_id)
        finally:
            lock.release()

    threading.Thread(target=_run, name=f"adhoc-{job_id}", daemon=True).start()
    return {"rc": 0, "log_id": log_id, "started": True}


def next_fire_times(job_id: str, n: int = 5) -> list[str]:
    """Preview the next N firings for a cron expression. Used by /schedule."""
    with db.session() as s:
        row = s.query(db.ScheduleEntry).filter_by(job_id=job_id).one_or_none()
        if row is None:
            return []
        cron = row.cron
    try:
        itr = croniter(cron, datetime.now(UTC))
    except Exception:
        return ["(invalid cron)"]
    out = []
    for _ in range(n):
        t: Any = itr.get_next(datetime)
        out.append(t.strftime("%Y-%m-%d %H:%M UTC"))
    return out


def shutdown() -> None:
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
