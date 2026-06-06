"""Committee transcript viewer for a single (date, symbol) decision.

Also hosts the QW11 notes + Stage 5 feedback/override/execution endpoints —
all of which key on the same (date, universe, symbol) tuple.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import load_run_record, template_ctx


router = APIRouter()


@router.get("/decisions/{date}/{symbol}", response_class=HTMLResponse)
async def decision_detail(request: Request, date: str, symbol: str, universe: str = "dow30"):
    from dipdiver.ui.app import templates

    rec = load_run_record(date, universe)
    if rec is None:
        raise HTTPException(404, f"no run record for {date}/{universe}")

    symbol_u = symbol.upper()
    decision = None
    for d in rec.get("committee_decisions", []):
        if d.get("symbol") == symbol_u:
            decision = d
            break
    if decision is None:
        raise HTTPException(404, f"no committee decision for {symbol_u} on {date}")

    # Notes + feedback history for this decision (QW11 / Stage 5)
    with db.session() as s:
        notes = (
            s.query(db.DecisionNote)
            .filter_by(date=date, universe=universe, symbol=symbol_u)
            .order_by(db.DecisionNote.created_utc.desc())
            .all()
        )
        notes_view = [
            {
                "note": n.note,
                "actor": n.actor,
                "created": n.created_utc.strftime("%Y-%m-%d %H:%M UTC"),
            }
            for n in notes
        ]
        feedback = (
            s.query(db.UserFeedback)
            .filter_by(date=date, universe=universe, symbol=symbol_u)
            .order_by(db.UserFeedback.created_utc.desc())
            .all()
        )
        feedback_view = [
            {
                "rating": f.rating,
                "notes": f.notes,
                "actor": f.actor,
                "created": f.created_utc.strftime("%Y-%m-%d %H:%M UTC"),
            }
            for f in feedback
        ]

    ctx = template_ctx(
        request,
        date=date,
        universe=universe,
        symbol=symbol_u,
        decision=decision,
        notes=notes_view,
        feedback=feedback_view,
    )
    return templates.TemplateResponse(request, "decision_detail.html", ctx)


# ---------------------------------------------------------------------------
# QW11 — note attached to a (date, universe, symbol)
# ---------------------------------------------------------------------------


@router.post("/decisions/{date}/{symbol}/note")
async def add_note(
    date: str,
    symbol: str,
    universe: str = "dow30",
    note: str = Form(...),
):
    note = (note or "").strip()
    if not note:
        raise HTTPException(422, "note text required")
    with db.session() as s:
        s.add(db.DecisionNote(
            date=date,
            universe=universe,
            symbol=symbol.upper(),
            note=note,
            created_utc=datetime.now(timezone.utc),
            actor="operator",
        ))
    return RedirectResponse(
        f"/decisions/{date}/{symbol}?universe={universe}",
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Stage 5 — feedback / override / execution
# ---------------------------------------------------------------------------


@router.post("/decisions/{date}/{symbol}/feedback")
async def add_feedback(
    date: str,
    symbol: str,
    universe: str = "dow30",
    rating: int = Form(...),
    notes: str = Form(""),
):
    if rating not in (-1, 0, 1):
        raise HTTPException(422, "rating must be -1, 0, or 1")
    with db.session() as s:
        s.add(db.UserFeedback(
            date=date, universe=universe, symbol=symbol.upper(),
            rating=int(rating), notes=(notes or "").strip(),
            created_utc=datetime.now(timezone.utc), actor="operator",
        ))
    return RedirectResponse(
        f"/decisions/{date}/{symbol}?universe={universe}",
        status_code=303,
    )


@router.post("/decisions/{date}/{symbol}/override")
async def add_override(
    date: str,
    symbol: str,
    universe: str = "dow30",
    new_decision: str = Form(...),
    reason: str = Form(...),
):
    reason = (reason or "").strip()
    if not reason:
        raise HTTPException(422, "reason is required for an override")
    if new_decision not in ("approved", "vetoed"):
        raise HTTPException(422, "new_decision must be 'approved' or 'vetoed'")
    rec = load_run_record(date, universe)
    original = "unknown"
    if rec is not None:
        for d in rec.get("committee_decisions", []):
            if d.get("symbol") == symbol.upper():
                original = "approved" if d.get("approved") else "vetoed"
                break
    with db.session() as s:
        s.add(db.OverrideDecision(
            date=date, universe=universe, symbol=symbol.upper(),
            original_decision=original, new_decision=new_decision,
            reason=reason,
            created_utc=datetime.now(timezone.utc), actor="operator",
        ))
    return RedirectResponse(
        f"/decisions/{date}/{symbol}?universe={universe}",
        status_code=303,
    )


@router.post("/decisions/{date}/{symbol}/execution")
async def add_execution(
    date: str,
    symbol: str,
    universe: str = "dow30",
    user_action: str = Form(...),
    fraction_taken: float | None = Form(None),
    notes: str = Form(""),
):
    if user_action not in ("taken", "skipped", "partial"):
        raise HTTPException(422, "user_action must be taken/skipped/partial")
    rec = load_run_record(date, universe)
    decision = "unknown"
    if rec is not None:
        for d in rec.get("committee_decisions", []):
            if d.get("symbol") == symbol.upper():
                decision = "approved" if d.get("approved") else "vetoed"
                break
    with db.session() as s:
        s.add(db.ExecutionRecord(
            date=date, universe=universe, symbol=symbol.upper(),
            decision=decision, user_action=user_action,
            fraction_taken=fraction_taken,
            notes=(notes or "").strip(),
            created_utc=datetime.now(timezone.utc), actor="operator",
        ))
    return RedirectResponse(
        f"/decisions/{date}/{symbol}?universe={universe}",
        status_code=303,
    )
