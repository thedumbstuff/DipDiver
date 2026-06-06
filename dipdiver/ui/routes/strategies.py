"""Strategies — per-strategy aggregate metrics."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dipdiver.harness.render import FusedDayRow
from dipdiver.ui.helpers import fmt_currency, fmt_pct, load_fused_rows, template_ctx


router = APIRouter()


def _aggregate(rows: list[FusedDayRow]) -> list[dict]:
    by_sid: dict[str, list[FusedDayRow]] = defaultdict(list)
    for r in rows:
        by_sid[r.strategy_id].append(r)
    out = []
    for sid, items in sorted(by_sid.items()):
        days = len(items)
        n_orders = sum(r.n_orders for r in items)
        n_proposed = sum(r.n_buys_proposed for r in items)
        n_reviewed = sum(r.n_buys_reviewed for r in items)
        n_vetoed = sum(r.n_buys_vetoed for r in items)
        rate = (n_vetoed / n_reviewed) if n_reviewed else None
        cost = sum(r.committee_cost_usd for r in items)
        out.append({
            "strategy_id": sid,
            "days": days,
            "n_orders": n_orders,
            "n_proposed": n_proposed,
            "n_reviewed": n_reviewed,
            "n_vetoed": n_vetoed,
            "veto_rate_fmt": fmt_pct(rate),
            "cost_fmt": f"${cost:.4f}",
            "latest": items[0].date if items else "—",
            "universe": items[0].universe if items else "—",
        })
    return out


@router.get("/strategies", response_class=HTMLResponse)
async def strategies(request: Request):
    from dipdiver.ui.app import templates

    rows = load_fused_rows()
    agg = _aggregate(rows)
    ctx = template_ctx(request, strategies=agg)
    return templates.TemplateResponse(request, "strategies.html", ctx)


@router.get("/strategies/{strategy_id}", response_class=HTMLResponse)
async def strategy_detail(request: Request, strategy_id: str):
    from dipdiver.ui.app import templates

    rows = [r for r in load_fused_rows() if r.strategy_id == strategy_id]
    agg = _aggregate(rows)
    summary = agg[0] if agg else None

    # Stage 7 / M14 — per-symbol attribution from PnlSettledEvent holdings.
    # When the holdings dict carries per-symbol market_value, sum total
    # exposure-days per symbol as a coarse attribution.
    from collections import Counter
    exposure_days: Counter[str] = Counter()
    realised_by_symbol: dict[str, float] = {}
    for r in rows:
        if r.submitted:
            for o in r.submitted.orders_submitted:
                if o.side == "buy":
                    exposure_days[o.symbol] += 1
        if r.pnl:
            # Equal-share within the day across held symbols
            held = list(r.pnl.holdings_at_close.keys())
            if held and r.pnl.realised_pnl_usd:
                share = r.pnl.realised_pnl_usd / len(held)
                for sym in held:
                    realised_by_symbol[sym] = realised_by_symbol.get(sym, 0.0) + share
    attribution = sorted(
        ({"symbol": s,
          "exposure_days": exposure_days.get(s, 0),
          "realised_pnl": realised_by_symbol.get(s, 0.0)}
         for s in set(exposure_days) | set(realised_by_symbol)),
        key=lambda x: x["realised_pnl"], reverse=True,
    )[:20]

    ctx = template_ctx(
        request,
        strategy_id=strategy_id,
        summary=summary,
        rows=rows,
        attribution=attribution,
        fmt_currency=fmt_currency,
        fmt_pct=fmt_pct,
    )
    return templates.TemplateResponse(request, "strategy_detail.html", ctx)


@router.get("/strategies-compare", response_class=HTMLResponse)
async def strategies_compare(request: Request, ids: str = ""):
    """Stage 7 / M14 — side-by-side A/B comparison.

    Usage: /strategies-compare?ids=dow30_lightgbm,dow30_lightgbm_committee
    """
    from dipdiver.ui.app import templates

    ids_list = [i.strip() for i in (ids or "").split(",") if i.strip()]
    rows = load_fused_rows()
    cols = []
    for sid in ids_list:
        s_rows = [r for r in rows if r.strategy_id == sid]
        agg = _aggregate(s_rows)
        cols.append({
            "strategy_id": sid,
            "summary": agg[0] if agg else None,
            "rows": s_rows[:30],
        })
    ctx = template_ctx(
        request,
        cols=cols,
        ids=ids_list,
        fmt_currency=fmt_currency,
        fmt_pct=fmt_pct,
    )
    return templates.TemplateResponse(request, "strategies_compare.html", ctx)
