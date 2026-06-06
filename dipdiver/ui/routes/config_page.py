"""Config page — form-driven editor for ui_config.yaml (strategies, alerts)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import template_ctx
from dipdiver.ui.settings import (
    StrategyConfig,
    UiConfig,
    reload_ui_config,
    save_ui_config,
    ui_config,
)


router = APIRouter()


@router.get("/config", response_class=HTMLResponse)
async def config_page(request: Request, saved: str | None = None):
    from dipdiver.ui.app import templates

    cfg = ui_config()
    with db.session() as s:
        history = (
            s.query(db.ConfigAudit)
            .order_by(db.ConfigAudit.saved_utc.desc())
            .limit(10)
            .all()
        )
        audit = [
            {
                "saved": h.saved_utc.strftime("%Y-%m-%d %H:%M UTC"),
                "actor": h.actor,
                "diff": h.diff_summary,
            }
            for h in history
        ]

    ctx = template_ctx(request, cfg=cfg, audit=audit, saved=saved)
    return templates.TemplateResponse(request, "config.html", ctx)


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
        last_modified_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
                saved_utc=datetime.now(timezone.utc),
                actor="operator",
                diff_summary=diff,
            )
        )
    return RedirectResponse("/config?saved=ok", status_code=303)
