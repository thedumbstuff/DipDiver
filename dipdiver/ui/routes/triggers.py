"""Triggers — manual one-shot job runs.

Each button submits a form that runs the job synchronously (small jobs) or
returns immediately and streams progress via SSE (long-running). For v1
we run inline; that's fine for nightly_run (~30s) and stub jobs.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from dipdiver.ui.helpers import template_ctx
from dipdiver.ui.jobs.registry import all_jobs
from dipdiver.ui.jobs.scheduler import trigger_now


router = APIRouter()


@router.get("/triggers", response_class=HTMLResponse)
async def triggers_page(request: Request):
    from dipdiver.ui.app import templates

    jobs = [{"job_id": j.job_id, "description": j.description} for j in all_jobs()]
    ctx = template_ctx(request, jobs=jobs)
    return templates.TemplateResponse(request, "triggers.html", ctx)


@router.post("/triggers/run", response_class=HTMLResponse)
async def trigger_run(request: Request, job_id: str = Form(...)):
    """Run a job synchronously and return the result as an HTMX fragment.

    If the same job is already running (manual race, or the cron firing at the
    same instant), return a 409 fragment instead of queueing a second run.
    """
    if not any(j.job_id == job_id for j in all_jobs()):
        raise HTTPException(404, f"unknown job {job_id}")

    result = trigger_now(job_id, triggered_by="manual")
    rc = result.get("rc", 0)

    # Busy: the per-job lock was held. Render a warning pill, NOT an error.
    # The fragment is returned with 200 so HTMX still swaps it in — we use
    # the body styling to signal "this didn't actually run."
    if result.get("busy"):
        return HTMLResponse(
            f"""
            <div class="mt-2">
              <span class="pill pill-warn">busy</span>
              <span class="text-xs text-zinc-400 ml-2">{result.get('error', 'already running')}</span>
            </div>
            """
        )

    body = json.dumps(result, indent=2, default=str)
    status_class = "pill-ok" if rc == 0 else "pill-err"
    return HTMLResponse(
        f"""
        <div class="mt-2">
          <span class="pill {status_class}">rc={rc}</span>
          <span class="text-xs text-zinc-400 ml-2">job_id={job_id}</span>
        </div>
        <pre class="mt-2 text-xs">{body}</pre>
        """
    )
