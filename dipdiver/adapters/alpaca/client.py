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
    """Minimal Alpaca trading client.

    Reads `ALPACA_API_KEY` and `ALPACA_API_SECRET` from environment. Defaults
    to paper=True. To enable live mode (real money), you must:

        1. Pass mode="live" explicitly.
        2. Have env DIPDIVER_LIVE_TRADING=true (extra belt-and-suspenders).
        3. Pass a `strategy_id` whose LiveTradingGate.check() returns passed=True.

    Otherwise raises LiveModeNotAllowedError. There is no path that flips the
    underlying alpaca-py `paper=False` flag without all three conditions met.

    The class keeps the legacy `AlpacaPaperClient` name to preserve imports;
    the `mode` kwarg is the discriminator.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        mode: str = "paper",
        strategy_id: str | None = None,
    ) -> None:
        if mode not in ("paper", "live"):
            raise ValueError(f"mode must be 'paper' or 'live', got {mode!r}")

        key = api_key or os.environ.get("ALPACA_API_KEY", "")
        secret = api_secret or os.environ.get("ALPACA_API_SECRET", "")
        if not key or not secret:
            raise RuntimeError(
                "ALPACA_API_KEY / ALPACA_API_SECRET not set. Get them from "
                "https://app.alpaca.markets/paper/dashboard/overview "
                "and put them in .env.m2."
            )

        self.mode = mode
        if mode == "live":
            # Stage 4 / M11 — three-way lock before we even construct the SDK.
            from dipdiver.adapters.alpaca.gate import (
                LiveModeNotAllowedError,
                LiveTradingGate,
            )
            env_ok = (os.environ.get("DIPDIVER_LIVE_TRADING") or "").lower() == "true"
            if not strategy_id:
                raise LiveModeNotAllowedError(
                    LiveTradingGate("unknown").check(),
                    missing_env=None,
                )
            gate_result = LiveTradingGate(strategy_id).check()
            if not (env_ok and gate_result.passed):
                raise LiveModeNotAllowedError(
                    gate_result,
                    missing_env=None if env_ok else "DIPDIVER_LIVE_TRADING=true",
                )

        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient

        self._trading = TradingClient(key, secret, paper=(mode == "paper"))
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
