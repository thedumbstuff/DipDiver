"""Config page — form-driven editor for ui_config.yaml (strategies, alerts),
plus the one-click "Add market" onboarding form (fetch → train → signals →
enable, run in the background via scheduler.run_adhoc)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import (
    job_busy_fragment,
    job_running_fragment,
    template_ctx,
)
from dipdiver.ui.jobs import market_onboard
from dipdiver.ui.jobs.scheduler import run_adhoc
from dipdiver.ui.settings import (
    StrategyConfig,
    UiConfig,
    reload_ui_config,
    save_ui_config,
    ui_config,
)

router = APIRouter()


def _universe_options() -> list[dict]:
    """Universe registry entries for the Add-market dropdown, flagging the
    ones that already have a configured strategy."""
    from dipdiver.brain.baselines.universes import UNIVERSES

    configured_cfgs = {s.m1_config for s in ui_config().strategies}
    out = []
    for u in UNIVERSES.values():
        out.append(
            {
                "key": u.name,
                "size": len(u),
                "live_executable": u.live_executable,
                "configured": any(
                    market_onboard.config_filename(u.name, mk) in configured_cfgs
                    for mk in market_onboard.MODEL_KINDS
                ),
            }
        )
    return out


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, saved: str | None = None):
    from dipdiver.ui.app import templates

    cfg = ui_config()
    with db.session() as s:
        history = s.query(db.ConfigAudit).order_by(db.ConfigAudit.saved_utc.desc()).limit(10).all()
        audit = [
            {
                "saved": h.saved_utc.strftime("%Y-%m-%d %H:%M UTC"),
                "actor": h.actor,
                "diff": h.diff_summary,
            }
            for h in history
        ]

    ctx = template_ctx(
        request,
        cfg=cfg,
        audit=audit,
        saved=saved,
        universes=_universe_options(),
        model_kinds=market_onboard.MODEL_KINDS,
    )
    return templates.TemplateResponse(request, "config.html", ctx)


@router.post("/config/markets/add", response_class=HTMLResponse)
async def markets_add(
    universe: str = Form(...),
    model: str = Form("lightgbm"),
    committee_variant: str | None = Form(None),
):
    """Start the market_onboard pipeline in the background.

    Returns the same polling fragment as /triggers — progress and the final
    result render in-place under the form.
    """
    from dipdiver.brain.baselines.universes import UNIVERSES

    if universe not in UNIVERSES:
        raise HTTPException(404, f"unknown universe {universe}")
    if model not in market_onboard.MODEL_KINDS:
        raise HTTPException(400, f"unknown model {model}")

    add_committee = committee_variant is not None

    result = run_adhoc(
        market_onboard.JOB_ID,
        lambda progress: market_onboard.run_onboard(universe, model, add_committee, progress),
        description=f"Onboard market {universe} ({model})",
        triggered_by="manual",
    )
    if result.get("busy"):
        return HTMLResponse(job_busy_fragment(result.get("error", "already running")))
    return HTMLResponse(job_running_fragment(result["log_id"], market_onboard.JOB_ID))


@router.post("/config/save")
async def config_save(request: Request):
    form = await request.form()
    strategies: list[StrategyConfig] = []
    # Form fields are repeated: strategy_id[], m1_config[], with_committee[], enabled[]
    sids = form.getlist("strategy_id")
    cfgs = form.getlist("m1_config")
    enableds = form.getlist("enabled")  # only present when checked
    committees = form.getlist("with_committee")
    for i, sid in enumerate(sids):
        sid = (sid or "").strip()
        if not sid:
            continue
        m1c = (cfgs[i] if i < len(cfgs) else "").strip()
        if not m1c:
            continue
        strategies.append(
            StrategyConfig(
                strategy_id=sid,
                m1_config=m1c,
                enabled=(f"e_{i}" in enableds),
                with_committee=(f"c_{i}" in committees),
            )
        )

    tz = (form.get("timezone") or "UTC").strip()
    telegram_chat_id = (form.get("telegram_chat_id") or "").strip() or None

    new_cfg = UiConfig(
        strategies=strategies,
        timezone=tz,
        telegram_chat_id=telegram_chat_id,
        last_modified_utc=datetime.now(UTC).isoformat(timespec="seconds"),
        last_modified_by="operator",
    )

    # Diff summary
    old = ui_config()
    parts: list[str] = []
    if len(old.strategies) != len(new_cfg.strategies):
        parts.append(f"strategies: {len(old.strategies)} → {len(new_cfg.strategies)}")
    if old.timezone != new_cfg.timezone:
        parts.append(f"tz: {old.timezone} → {new_cfg.timezone}")
    if old.telegram_chat_id != new_cfg.telegram_chat_id:
        parts.append("telegram_chat_id changed")
    diff = "; ".join(parts) or "minor edits"

    save_ui_config(new_cfg)
    reload_ui_config()
    with db.session() as s:
        s.add(
            db.ConfigAudit(
                saved_utc=datetime.now(UTC),
                actor="operator",
                diff_summary=diff,
            )
        )
    return RedirectResponse("/config?saved=ok", status_code=303)
