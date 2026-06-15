"""Triggers — manual one-shot job runs.

Each button POSTs to /triggers/run, which starts the job in a background
thread and returns an HTMX fragment immediately. The fragment polls
/triggers/status/{log_id} every 2s (reading the JobLog row the job writes)
until the run finishes, so long jobs never block the event loop — the rest
of the UI stays responsive while a job runs.

/triggers/status is generic over JobLog rows: ad-hoc jobs started elsewhere
(e.g. /config "Add market" via scheduler.run_adhoc) poll it too.
"""

from __future__ import annotations

import html

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import (
    job_busy_fragment,
    job_finished_fragment,
    job_running_fragment,
    template_ctx,
)
from dipdiver.ui.jobs.registry import all_jobs
from dipdiver.ui.jobs.scheduler import trigger_async

router = APIRouter()


@router.get("/triggers", response_class=HTMLResponse)
async def triggers_page(request: Request):
    from dipdiver.ui.app import templates

    jobs = [{"job_id": j.job_id, "description": j.description} for j in all_jobs()]
    ctx = template_ctx(request, jobs=jobs)
    return templates.TemplateResponse(request, "triggers.html", ctx)


@router.post("/triggers/run", response_class=HTMLResponse)
async def trigger_run(request: Request, job_id: str = Form(...)):
    """Start a job in the background and return a polling fragment.

    If the same job is already running (manual race, or the cron firing at the
    same instant), return a busy fragment instead of queueing a second run.
    """
    if not any(j.job_id == job_id for j in all_jobs()):
        raise HTTPException(404, f"unknown job {job_id}")

    result = trigger_async(job_id, triggered_by="manual")

    # Busy: the per-job lock was held. Render a warning pill, NOT an error —
    # the fragment is returned with 200 so HTMX still swaps it in.
    if result.get("busy"):
        return HTMLResponse(job_busy_fragment(result.get("error", "already running")))

    if "log_id" not in result:
        return HTMLResponse(f"""
            <div class="mt-2">
              <span class="pill pill-err">rc={result.get('rc', 1)}</span>
              <span class="text-xs text-zinc-400 ml-2">{html.escape(result.get('error', 'failed to start'))}</span>
            </div>
            """)

    return HTMLResponse(job_running_fragment(result["log_id"], job_id))


@router.get("/triggers/status/{log_id}", response_class=HTMLResponse)
async def trigger_status(log_id: int):
    """One poll tick: re-render the run's status from its JobLog row."""
    with db.session() as s:
        row = s.get(db.JobLog, log_id)
    if row is None:
        return HTMLResponse(f"""
            <div class="mt-2">
              <span class="pill pill-err">gone</span>
              <span class="text-xs text-zinc-400 ml-2">no JobLog row #{log_id}</span>
            </div>
            """)
    if row.status == "running":
        return HTMLResponse(job_running_fragment(row.id, row.job_id, summary=row.summary or ""))
    return HTMLResponse(job_finished_fragment(row))
