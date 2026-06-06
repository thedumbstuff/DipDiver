"""Scoreboard page — live render of the JSONL into the same Markdown tables
that scripts/m6_render_scoreboard.py produces, but inline in the UI.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from dipdiver.harness.render import (
    fuse_by_day,
    render_full_report,
    render_markdown_table,
    render_strategy_summary,
)
from dipdiver.harness.scoreboard import read_events
from dipdiver.ui.helpers import (
    fmt_currency,
    fmt_pct,
    load_fused_rows,
    template_ctx,
)


router = APIRouter()


@router.get("/scoreboard", response_class=HTMLResponse)
async def scoreboard(request: Request):
    from dipdiver.ui.app import templates

    rows = load_fused_rows()
    ctx = template_ctx(
        request,
        rows=rows,
        fmt_currency=fmt_currency,
        fmt_pct=fmt_pct,
    )
    return templates.TemplateResponse(request, "scoreboard.html", ctx)


@router.get("/scoreboard.md", response_class=PlainTextResponse)
async def scoreboard_markdown():
    """Same content as scripts/m6_render_scoreboard.py --out. Useful for curl."""
    events = read_events()
    rows = fuse_by_day(events)
    return render_full_report(rows)
