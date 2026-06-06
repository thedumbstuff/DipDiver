"""Schedule editor — edit cron expressions for every registered job."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import template_ctx
from dipdiver.ui.jobs.scheduler import next_fire_times, update_schedule


router = APIRouter()


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request, saved: str | None = None):
    from dipdiver.ui.app import templates

    with db.session() as s:
        rows = s.query(db.ScheduleEntry).order_by(db.ScheduleEntry.job_id).all()
        items = [
            {
                "job_id": r.job_id,
                "cron": r.cron,
                "enabled": r.enabled,
                "description": r.description,
                "next_fires": next_fire_times(r.job_id, 3),
            }
            for r in rows
        ]
    ctx = template_ctx(request, items=items, saved=saved)
    return templates.TemplateResponse(request, "schedule.html", ctx)


@router.post("/schedule/save")
async def schedule_save(
    request: Request,
    job_id: str = Form(...),
    cron: str = Form(...),
    enabled: str | None = Form(None),
):
    try:
        update_schedule(job_id, cron.strip(), enabled is not None)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/schedule?saved=error:{type(e).__name__}", status_code=303)
    return RedirectResponse("/schedule?saved=ok", status_code=303)
