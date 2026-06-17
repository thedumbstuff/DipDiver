"""Model lifecycle viewer — `/models`.

Shows every ModelVersion row, sorted newest first, with status + key metrics.
The dashboard uses `latest_locked_version()` to surface a "model age" badge.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dipdiver.ui import db
from dipdiver.ui.helpers import template_ctx


router = APIRouter()


def latest_locked_version(config_name: str) -> dict | None:
    """Return the newest `locked` ModelVersion for a config_name, or None."""
    with db.session() as s:
        row = (
            s.query(db.ModelVersion)
            .filter(db.ModelVersion.config_name == config_name)
            .filter(db.ModelVersion.status == "locked")
            .order_by(db.ModelVersion.locked_on_utc.desc())
            .first()
        )
        if row is None:
            return None
        return {
            "config_name": row.config_name,
            "config_hash": row.config_hash,
            "locked_on": row.locked_on_utc,
            "train_start": row.train_start,
            "train_end": row.train_end,
            "test_start": row.test_start,
            "test_end": row.test_end,
            "sharpe": row.sharpe, "max_dd": row.max_dd, "hit_rate": row.hit_rate,
            "psr": row.psr,
        }


def model_age_badge(test_end_iso: str | None) -> dict:
    """Returns {tone, label, days_to_expiry}. Red when expired, yellow within 30d."""
    if not test_end_iso:
        return {"tone": "muted", "label": "no model", "days_to_expiry": None}
    try:
        end = datetime.fromisoformat(test_end_iso).date()
    except ValueError:
        return {"tone": "muted", "label": test_end_iso, "days_to_expiry": None}
    today = datetime.now(timezone.utc).date()
    delta = (end - today).days
    if delta < 0:
        return {"tone": "err", "label": f"expired {abs(delta)}d ago",
                "days_to_expiry": delta}
    if delta <= 30:
        return {"tone": "warn", "label": f"{delta}d to expiry",
                "days_to_expiry": delta}
    return {"tone": "ok", "label": f"valid {delta}d", "days_to_expiry": delta}


@router.get("/models", response_class=HTMLResponse)
async def models_page(request: Request):
    from dipdiver.ui.app import templates

    db.init_db()
    with db.session() as s:
        rows = (
            s.query(db.ModelVersion)
            .order_by(db.ModelVersion.locked_on_utc.desc())
            .all()
        )
        view = [
            {
                "id": r.id,
                "config_name": r.config_name,
                "config_hash": r.config_hash[:10],
                "locked_on": r.locked_on_utc.strftime("%Y-%m-%d %H:%M UTC"),
                "train_start": r.train_start,
                "train_end": r.train_end,
                "test_start": r.test_start,
                "test_end": r.test_end,
                "sharpe": r.sharpe,
                "max_dd": r.max_dd,
                "hit_rate": r.hit_rate,
                "status": r.status,
                "notes": r.notes,
                "age": model_age_badge(r.test_end),
            }
            for r in rows
        ]
    ctx = template_ctx(request, rows=view)
    return templates.TemplateResponse(request, "models.html", ctx)
