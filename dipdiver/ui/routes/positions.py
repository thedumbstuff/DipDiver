"""Live Alpaca paper positions snapshot."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dipdiver.ui.helpers import template_ctx


router = APIRouter()


@router.get("/positions", response_class=HTMLResponse)
async def positions(request: Request):
    from dipdiver.ui.app import templates

    error: str | None = None
    positions_list: list[dict] = []
    account: dict | None = None
    try:
        from dipdiver.adapters.alpaca.client import AlpacaPaperClient
        c = AlpacaPaperClient()
        a = c.get_account()
        account = {
            "equity": a.equity,
            "cash": a.cash,
            "buying_power": a.buying_power,
            "status": str(a.status),
        }
        for p in c.get_positions():
            positions_list.append({
                "symbol": p.symbol,
                "qty": float(p.qty or 0),
                "side": getattr(p, "side", ""),
                "market_value": float(getattr(p, "market_value", 0) or 0),
                "avg_entry_price": float(getattr(p, "avg_entry_price", 0) or 0),
                "unrealised_pl": float(getattr(p, "unrealized_pl", 0) or 0),
                "unrealised_plpc": float(getattr(p, "unrealized_plpc", 0) or 0),
            })
        positions_list.sort(key=lambda x: x["market_value"], reverse=True)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"

    ctx = template_ctx(request, account=account, positions=positions_list, error=error)
    return templates.TemplateResponse(request, "positions.html", ctx)
