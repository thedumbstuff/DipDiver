"""Health page — Alpaca connection, scheduler status, last runs, kill-switch."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import template_ctx, time_ago
from dipdiver.ui.jobs.alerts import send_alert
from dipdiver.ui.jobs.scheduler import get_scheduler


router = APIRouter()


def _alpaca_status() -> dict:
    try:
        from dipdiver.adapters.alpaca.client import AlpacaPaperClient
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"adapter import failed: {e}"}
    try:
        c = AlpacaPaperClient()
        acct = c.get_account()
        return {
            "ok": True,
            "status": str(acct.status),
            "equity": acct.equity,
            "cash": acct.cash,
            "buying_power": acct.buying_power,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "message": f"{type(e).__name__}: {e}"}


def _live_gate_summary() -> list[dict]:
    """Stage 4 / M11 — evaluate the live-trading gate for each enabled strategy."""
    try:
        from dipdiver.adapters.alpaca.gate import LiveTradingGate
        from dipdiver.ui.settings import ui_config
    except Exception:  # noqa: BLE001
        return []
    cfg = ui_config()
    out: list[dict] = []
    for s in cfg.strategies:
        if not s.enabled:
            continue
        try:
            res = LiveTradingGate(s.strategy_id).check()
            out.append({
                "strategy_id": s.strategy_id,
                "passed": res.passed,
                "criteria": [
                    {
                        "name": c.name, "threshold": c.threshold,
                        "actual": c.actual, "passed": c.passed,
                        "message": c.message,
                    }
                    for c in res.criteria
                ],
            })
        except Exception as e:  # noqa: BLE001
            out.append({"strategy_id": s.strategy_id, "error": str(e)})
    return out


@router.get("/health", response_class=HTMLResponse)
async def health(request: Request):
    from dipdiver.ui.app import templates

    sched = get_scheduler()
    scheduler_ok = sched.running
    jobs = [
        {
            "id": j.id,
            "name": j.name,
            "next": j.next_run_time.strftime("%Y-%m-%d %H:%M UTC") if j.next_run_time else "—",
        }
        for j in sched.get_jobs()
    ]

    alpaca = _alpaca_status()
    live_gates = _live_gate_summary()

    # Show which .env file actually populated credentials (helps debug the
    # "I put my keys in .env.m2.example, why doesn't it work" case).
    from dipdiver.ui import env_loader
    env_report = env_loader.last_report()
    env_info: dict | None = None
    if env_report is not None:
        env_info = {
            "files_loaded": [
                # Just the basename for display; full path is in logs.
                p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
                for p in env_report.files_loaded
            ],
            "vars_set": list(env_report.vars_set),
            "vars_skipped_placeholder": list(env_report.vars_skipped_placeholder),
            "vars_skipped_already_set": list(env_report.vars_skipped_already_set),
        }

    with db.session() as s:
        last_logs = (
            s.query(db.JobLog)
            .order_by(db.JobLog.started_utc.desc())
            .limit(10)
            .all()
        )
        recent = [
            {
                "job_id": r.job_id,
                "status": r.status,
                "started": r.started_utc.strftime("%Y-%m-%d %H:%M UTC") if r.started_utc else "—",
                "ago": time_ago(r.started_utc),
                "summary": r.summary or "",
                "error": r.error or "",
            }
            for r in last_logs
        ]
        kse = (
            s.query(db.KillSwitchEvent)
            .order_by(db.KillSwitchEvent.triggered_utc.desc())
            .limit(5)
            .all()
        )
        kill_history = [
            {
                "when": k.triggered_utc.strftime("%Y-%m-%d %H:%M UTC"),
                "ago": time_ago(k.triggered_utc),
                "reason": k.reason,
                "status": k.status,
                "actions": k.actions_taken,
            }
            for k in kse
        ]

    ctx = template_ctx(
        request,
        scheduler_ok=scheduler_ok,
        jobs=jobs,
        alpaca=alpaca,
        recent_logs=recent,
        kill_history=kill_history,
        live_gates=live_gates,
        env_info=env_info,
        health_ok=alpaca.get("ok", False) and scheduler_ok,
    )
    return templates.TemplateResponse(request, "health.html", ctx)


@router.post("/health/kill-switch")
async def kill_switch(
    request: Request,
    reason: str = Form(...),
    confirm: str = Form(...),
):
    """Cancel all open orders + flatten positions + pause nightly job."""
    if confirm.strip().upper() != "FLATTEN":
        return RedirectResponse("/health?error=must-type-FLATTEN", status_code=303)

    actions: list[str] = []
    status = "succeeded"
    try:
        from dipdiver.adapters.alpaca.client import AlpacaPaperClient
        c = AlpacaPaperClient()
        try:
            c.client.cancel_orders()
            actions.append("cancelled all open orders")
        except Exception as e:  # noqa: BLE001
            actions.append(f"cancel_orders FAILED: {e}")
            status = "partial"
        try:
            c.client.close_all_positions(cancel_orders=True)
            actions.append("close_all_positions issued")
        except Exception as e:  # noqa: BLE001
            actions.append(f"close_all_positions FAILED: {e}")
            status = "partial"
    except Exception as e:  # noqa: BLE001
        actions.append(f"alpaca client init FAILED: {e}")
        status = "failed"

    # Disable nightly job
    try:
        from dipdiver.ui.jobs.scheduler import update_schedule
        with db.session() as s:
            row = s.query(db.ScheduleEntry).filter_by(job_id="nightly_run").one_or_none()
            current_cron = row.cron if row else "35 14 * * 1-5"
        update_schedule("nightly_run", current_cron, enabled=False)
        actions.append("disabled nightly_run job")
    except Exception as e:  # noqa: BLE001
        actions.append(f"disable nightly_run FAILED: {e}")
        status = "partial" if status == "succeeded" else status

    with db.session() as s:
        s.add(
            db.KillSwitchEvent(
                triggered_utc=datetime.now(timezone.utc),
                reason=reason.strip()[:2000],
                actor="operator",
                actions_taken="\n".join(actions),
                status=status,
            )
        )

    send_alert(
        f"🚨 KILL SWITCH activated: {reason.strip()[:200]} — status={status}",
        severity="error",
    )
    return RedirectResponse(f"/health?kill={status}", status_code=303)
