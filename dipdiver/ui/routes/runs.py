"""Runs — list of daily strategy submissions + single-day deep dive."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from dipdiver.ui.helpers import (
    fmt_currency,
    fmt_pct,
    load_fused_rows,
    load_run_record,
    template_ctx,
)


router = APIRouter()


@router.get("/runs", response_class=HTMLResponse)
async def runs_list(request: Request, strategy: str | None = None, universe: str | None = None):
    from dipdiver.ui.app import templates

    rows = load_fused_rows()
    if strategy:
        rows = [r for r in rows if r.strategy_id == strategy]
    if universe:
        rows = [r for r in rows if r.universe == universe]

    strategy_ids = sorted({r.strategy_id for r in load_fused_rows()})
    universes = sorted({r.universe for r in load_fused_rows()})

    ctx = template_ctx(
        request,
        rows=rows,
        strategy_ids=strategy_ids,
        universes=universes,
        filter_strategy=strategy or "",
        filter_universe=universe or "",
        fmt_currency=fmt_currency,
        fmt_pct=fmt_pct,
    )
    return templates.TemplateResponse(request, "runs_list.html", ctx)


@router.get("/runs/{date}", response_class=HTMLResponse)
async def run_detail(request: Request, date: str, universe: str = "dow30"):
    from dipdiver.ui.app import templates

    rec = load_run_record(date, universe)
    if rec is None:
        raise HTTPException(404, f"no run record for {date}/{universe}")
    # Find the matching fused row for top-line metadata
    matching = [r for r in load_fused_rows() if r.date == date and r.universe == universe]
    row = matching[0] if matching else None

    ctx = template_ctx(
        request,
        rec=rec,
        row=row,
        universe=universe,
        date=date,
        fmt_currency=fmt_currency,
        fmt_pct=fmt_pct,
    )
    return templates.TemplateResponse(request, "run_detail.html", ctx)
