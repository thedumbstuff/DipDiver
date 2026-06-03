"""Thin alpaca-py wrapper that exposes only what the live runner needs.

Keeps the import of alpaca-py lazy so the rest of the package (and our
testing without the m3 extras installed) doesn't blow up.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountSnapshot:
    cash: float
    equity: float
    buying_power: float
    status: str


@dataclass(frozen=True)
class PositionSnapshot:
    symbol: str
    qty: float
    market_value: float
    cost_basis: float
    unrealized_pl: float


class AlpacaPaperClient:
    """Minimal Alpaca paper-trading client.

    Reads `ALPACA_API_KEY` and `ALPACA_API_SECRET` from environment. Always
    paper=True; we do not provide a live-money mode here.
    """

    def __init__(self, api_key: str | None = None, api_secret: str | None = None) -> None:
        key = api_key or os.environ.get("ALPACA_API_KEY", "")
        secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        if not key or not secret:
            raise RuntimeError(
                "ALPACA_API_KEY / ALPACA_API_SECRET not set. Get them from "
                "https://app.alpaca.markets/paper/dashboard/overview "
                "and put them in .env.m2."
            )

        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        self._trading = TradingClient(key, secret, paper=True)
        self._data = StockHistoricalDataClient(key, secret)

    # ---- account / clock ---------------------------------------------------

    def get_account(self) -> AccountSnapshot:
        a = self._trading.get_account()
        return AccountSnapshot(
            cash=float(a.cash),
            equity=float(a.equity),
            buying_power=float(a.buying_power),
            status=str(a.status),
        )

    def market_is_open(self) -> bool:
        clock = self._trading.get_clock()
        return bool(clock.is_open)

    # ---- positions ---------------------------------------------------------

    def get_positions(self) -> list[PositionSnapshot]:
        positions = self._trading.get_all_positions()
        out: list[PositionSnapshot] = []
        for p in positions:
            qty = float(p.qty)
            if qty <= 0:
                continue
            out.append(PositionSnapshot(
                symbol=str(p.symbol),
                qty=qty,
                market_value=float(p.market_value),
                cost_basis=float(p.cost_basis),
                unrealized_pl=float(p.unrealized_pl),
            ))
        return out

    # ---- orders ------------------------------------------------------------

    def open_position(self, symbol: str, notional_usd: float) -> dict[str, Any]:
        """Place a market BUY for `notional_usd` worth of `symbol`."""
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        req = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional_usd, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = self._trading.submit_order(req)
        return {
            "id": str(order.id),
            "symbol": symbol,
            "side": "buy",
            "notional": round(notional_usd, 2),
            "status": str(order.status),
            "submitted_at": str(order.submitted_at),
        }

    def close_position(self, symbol: str) -> dict[str, Any]:
        """Close the entire position in `symbol` via market sell."""
        order = self._trading.close_position(symbol)
        return {
            "id": str(getattr(order, "id", "")),
            "symbol": symbol,
            "side": "sell",
            "status": str(getattr(order, "status", "")),
            "submitted_at": str(getattr(order, "submitted_at", "")),
        }
