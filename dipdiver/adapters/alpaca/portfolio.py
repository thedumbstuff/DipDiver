"""Alpaca portfolio P&L fetcher for the M6.2 forward-eval loop.

For each trading day we want one row per (date, universe, strategy_id):
realised + unrealised P&L, equity at close, holdings snapshot. The Alpaca
account is shared across strategies, so when multiple strategies submitted
on the same day we attribute proportionally by submitted notional.

The Alpaca SDK is imported lazily so tests can monkey-patch
`_get_trading_client_fn` without alpaca-py installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls
from typing import Any, Callable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DailyPnlSnapshot:
    """End-of-day account snapshot for one trading date.

    The fields here are account-wide. Per-strategy attribution happens in
    `pnl_settle.run()` by multiplying by `attribution_weight`.
    """

    date: str  # YYYY-MM-DD
    realised_pnl_usd: float
    unrealised_pnl_usd: float
    equity_at_close: float
    holdings_at_close: dict[str, float]  # symbol -> market_value
    slippage_usd: float | None = None
    commission_usd: float | None = None


# ---------------------------------------------------------------------------
# Provider abstraction — tests inject a fake
# ---------------------------------------------------------------------------


# A PnlProvider takes a target_date and returns a DailyPnlSnapshot. Tests pass
# in a fake; production uses _make_alpaca_provider().
PnlProvider = Callable[[date_cls], DailyPnlSnapshot]


def _make_alpaca_provider() -> PnlProvider:
    """Real Alpaca-backed provider. Constructs lazily so missing alpaca-py
    or missing credentials don't break import.
    """
    from dipdiver.adapters.alpaca.client import AlpacaPaperClient

    client = AlpacaPaperClient()

    def _provider(target_date: date_cls) -> DailyPnlSnapshot:
        return fetch_daily_pnl_via_alpaca(client, target_date)

    return _provider


def fetch_daily_pnl_via_alpaca(client: Any, target_date: date_cls) -> DailyPnlSnapshot:
    """Pull one day's P&L from an Alpaca client.

    Strategy: query portfolio_history for the last 7 calendar days at 1D
    timeframe, find the row matching `target_date`, derive total P&L from
    the equity delta vs the prior point, then split realised vs unrealised
    via current open positions.

    This is account-wide. Multi-strategy attribution is the caller's job.
    """
    from alpaca.trading.requests import GetPortfolioHistoryRequest

    req = GetPortfolioHistoryRequest(
        period="7D",
        timeframe="1D",
        extended_hours=False,
    )
    history = client._trading.get_portfolio_history(req)

    # `equity` is the list of EOD account equity, parallel to `timestamp`.
    equity_series = [float(x) for x in (history.equity or [])]
    pl_series = [float(x) for x in (history.profit_loss or [])]
    ts_series = list(history.timestamp or [])
    base_value = float(history.base_value or 0.0)

    if not equity_series or not ts_series:
        raise RuntimeError(
            f"Alpaca portfolio_history returned empty series for {target_date}; "
            "cannot compute daily P&L."
        )

    # Match by date — Alpaca returns unix seconds at market-close per row.
    import datetime as _dt
    target_ymd = target_date.isoformat()
    idx: int | None = None
    for i, ts in enumerate(ts_series):
        d = _dt.datetime.utcfromtimestamp(int(ts)).date()
        if d.isoformat() == target_ymd:
            idx = i
            break
    if idx is None:
        raise RuntimeError(
            f"Alpaca portfolio_history has no row matching {target_ymd}. "
            f"Series covers: {[_dt.datetime.utcfromtimestamp(int(t)).date().isoformat() for t in ts_series]}"
        )

    equity_at_close = equity_series[idx]
    # profit_loss is delta-from-prior-base per row in Alpaca's series. If the
    # field is unreliable on a given account, fall back to equity delta.
    day_pl = pl_series[idx] if idx < len(pl_series) else (
        equity_at_close - (equity_series[idx - 1] if idx > 0 else base_value)
    )

    # Unrealised = sum of currently-open positions' unrealised_pl. Realised
    # is the residual. This snapshot is "right now" not "as of target_date";
    # acceptable when run T+1 morning before any new fills.
    positions = client.get_positions()
    unrealised_pnl = sum(p.unrealized_pl for p in positions)
    realised_pnl = day_pl - unrealised_pnl
    holdings = {p.symbol: float(p.market_value) for p in positions}

    return DailyPnlSnapshot(
        date=target_ymd,
        realised_pnl_usd=round(realised_pnl, 2),
        unrealised_pnl_usd=round(unrealised_pnl, 2),
        equity_at_close=round(equity_at_close, 2),
        holdings_at_close=holdings,
        slippage_usd=None,    # Alpaca does not report aggregate slippage; M14
        commission_usd=None,  # Alpaca paper is commission-free
    )


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyShare:
    """Per-strategy share of one day's account-wide P&L.

    `weight` is the fraction of submitted-notional across all strategies on
    this date. For sole-strategy days, `weight == 1.0` and
    `attribution_method == "single_strategy"`.
    """

    universe: str
    strategy_id: str
    notional_submitted_usd: float
    weight: float
    attribution_method: str


def attribute_strategies(submitted_events: list[Any]) -> list[StrategyShare]:
    """Split one day's account-wide P&L across the strategies that submitted.

    `submitted_events` must be the `DaySubmittedEvent` rows for a single date.
    If exactly one event exists, that strategy gets weight=1.0. Otherwise
    weights are proportional to total submitted notional (sells with notional=None
    are skipped — they don't represent capital deployment).
    """
    if not submitted_events:
        return []
    if len(submitted_events) == 1:
        e = submitted_events[0]
        notional = sum(
            (o.notional_usd or 0.0) for o in e.orders_submitted
        )
        return [StrategyShare(
            universe=e.universe,
            strategy_id=e.strategy_id,
            notional_submitted_usd=notional,
            weight=1.0,
            attribution_method="single_strategy",
        )]
    # Multi-strategy day — normalise by notional.
    notionals: list[tuple[Any, float]] = []
    total = 0.0
    for e in submitted_events:
        n = sum((o.notional_usd or 0.0) for o in e.orders_submitted)
        notionals.append((e, n))
        total += n
    if total <= 0:
        # No-trade day for all strategies — split equally so we still record
        # the snapshot (P&L will be ~0 anyway).
        eq = 1.0 / len(submitted_events)
        return [
            StrategyShare(
                universe=e.universe,
                strategy_id=e.strategy_id,
                notional_submitted_usd=0.0,
                weight=eq,
                attribution_method="weighted_by_notional",
            )
            for e, _ in notionals
        ]
    return [
        StrategyShare(
            universe=e.universe,
            strategy_id=e.strategy_id,
            notional_submitted_usd=n,
            weight=n / total,
            attribution_method="weighted_by_notional",
        )
        for e, n in notionals
    ]
