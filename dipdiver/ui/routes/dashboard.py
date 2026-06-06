"""Dashboard — landing page; today's status + 7-day overview."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import (
    fmt_currency,
    fmt_pct,
    load_fused_rows,
    template_ctx,
    time_ago,
)
from dipdiver.ui.jobs.scheduler import get_scheduler


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    from dipdiver.ui.app import templates

    rows = load_fused_rows()
    today_row = rows[0] if rows else None

    # 7-day aggregate: sum cost, count vetos
    last7 = rows[:7]
    n_days = len(last7)
    total_orders = sum(r.n_orders for r in last7)
    total_buys = sum(r.n_buys_reviewed for r in last7)
    total_vetoed = sum(r.n_buys_vetoed for r in last7)
    veto_rate = (total_vetoed / total_buys) if total_buys else None
    cost7 = sum(r.committee_cost_usd for r in last7)

    # Next scheduled run
    sched = get_scheduler()
    next_runs = []
    for job in sched.get_jobs():
        nt = job.next_run_time
        if nt is None:
            continue
        next_runs.append((nt, job.id, job.name))
    next_runs.sort()
    upcoming = [
        {"when": nt.strftime("%Y-%m-%d %H:%M UTC"), "job_id": jid, "name": nm}
        for nt, jid, nm in next_runs[:5]
    ]

    # Recent job log
    with db.session() as s:
        recent_logs = (
            s.query(db.JobLog)
            .order_by(db.JobLog.started_utc.desc())
            .limit(5)
            .all()
        )
        recent = [
            {
                "job_id": r.job_id,
                "status": r.status,
                "started_ago": time_ago(r.started_utc),
                "summary": r.summary or r.error or "",
            }
            for r in recent_logs
        ]

    ctx = template_ctx(
        request,
        today=today_row,
        rows7=last7,
        last7_summary={
            "days": n_days,
            "total_orders": total_orders,
            "total_buys": total_buys,
            "total_vetoed": total_vetoed,
            "veto_rate": fmt_pct(veto_rate),
            "cost": f"${cost7:.4f}",
        },
        upcoming=upcoming,
        recent_logs=recent,
        fmt_currency=fmt_currency,
        fmt_pct=fmt_pct,
    )
    return templates.TemplateResponse(request, "dashboard.html", ctx)
