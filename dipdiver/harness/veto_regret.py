"""Counterfactual P&L for committee-vetoed buys.

For every `CommitteeVerdictSummary` with `approved=False` on day D, this module
asks: what would the symbol have done if we'd taken the buy and held it for N
trading days? If the answer is consistently positive across many vetoes, the
committee is costing us money (which is the trigger for the ADR-003 demotion
of the risk persona to "annotation only").

The price source is pluggable. Production uses Qlib's existing OHLCV store
(no extra data fetch needed). Tests pass in a dict-backed fake.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from typing import Callable, Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VetoRegret:
    """Result of one (symbol, entry_date, hold_days) lookup."""

    symbol: str
    entry_date: str        # YYYY-MM-DD — the trading day of the (vetoed) decision
    settle_date: str       # YYYY-MM-DD — the close used for the counterfactual exit
    estimated_entry_price: float
    actual_price_at_settle: float
    counterfactual_pnl_pct: float  # (settle - entry) / entry
    holding_window_days: int


# ---------------------------------------------------------------------------
# Price provider protocol
# ---------------------------------------------------------------------------


class PriceProvider(Protocol):
    def close_on_or_before(self, symbol: str, target_date: date_cls) -> tuple[date_cls, float] | None:
        """Return (actual_date, close_price) for `symbol` on or before
        `target_date`. None if the symbol or date isn't covered.

        We accept "on or before" rather than exact-match because trading
        calendars have holidays; the most recent close still tells us what the
        counterfactual P&L is at that moment.
        """
        ...


# ---------------------------------------------------------------------------
# Default Qlib-backed provider
# ---------------------------------------------------------------------------


class QlibPriceProvider:
    """Reads from Qlib's $close field via the same provider_uri the M1
    pipeline uses. Lazy-imports qlib so test environments without it are fine.
    """

    def __init__(self, provider_uri: str | None = None, region: str = "us") -> None:
        self._uri = provider_uri
        self._region = region
        self._initialised = False

    def _ensure_init(self) -> None:
        if self._initialised:
            return
        from qlib.constant import REG_US

        from dipdiver.brain.baselines._qlib.init import safe_qlib_init
        safe_qlib_init(provider_uri=self._uri, region=REG_US if self._region == "us" else self._region)
        self._initialised = True

    def close_on_or_before(self, symbol: str, target_date: date_cls) -> tuple[date_cls, float] | None:
        try:
            self._ensure_init()
            from qlib.data import D
            # Qlib's calendar-aware fetch — look back up to 14 calendar days
            start = (target_date - timedelta(days=14)).isoformat()
            end = target_date.isoformat()
            df = D.features([symbol.upper()], ["$close"], start_time=start, end_time=end)
            if df is None or df.empty:
                return None
            # Pick last row at or before target_date.
            df = df.reset_index()
            df = df[df["datetime"].dt.date <= target_date]
            if df.empty:
                return None
            row = df.iloc[-1]
            return (row["datetime"].date(), float(row["$close"]))
        except Exception as e:  # noqa: BLE001
            log.debug("Qlib lookup for %s@%s failed: %s", symbol, target_date, e)
            return None


# ---------------------------------------------------------------------------
# Dict-backed test provider
# ---------------------------------------------------------------------------


@dataclass
class DictPriceProvider:
    """In-memory provider keyed by (symbol, YYYY-MM-DD). For tests."""

    prices: dict[tuple[str, str], float]

    def close_on_or_before(self, symbol: str, target_date: date_cls) -> tuple[date_cls, float] | None:
        # Walk back up to 7 days to mimic the holiday-aware Qlib behaviour.
        for offset in range(0, 8):
            probe = target_date - timedelta(days=offset)
            key = (symbol.upper(), probe.isoformat())
            if key in self.prices:
                return (probe, self.prices[key])
        return None


# ---------------------------------------------------------------------------
# Core counterfactual computation
# ---------------------------------------------------------------------------


def compute_counterfactual(
    *,
    symbol: str,
    entry_date: date_cls,
    holding_window_days: int,
    provider: PriceProvider,
    today_utc: date_cls | None = None,
) -> VetoRegret | None:
    """Return a `VetoRegret` for (symbol, entry_date) held for `holding_window_days`.

    Returns None when:
      - the entry close isn't available (data gap),
      - the settle date hasn't materialised yet (`settle_date > today_utc`),
      - or the settle close isn't available.

    `holding_window_days` is calendar days; trading-day approximation is fine
    here because the provider implements "on or before".
    """
    if today_utc is None:
        today_utc = datetime.utcnow().date()
    settle_target = entry_date + timedelta(days=holding_window_days)
    if settle_target > today_utc:
        return None  # not enough time elapsed
    entry = provider.close_on_or_before(symbol, entry_date)
    settle = provider.close_on_or_before(symbol, settle_target)
    if entry is None or settle is None:
        return None
    entry_actual_date, entry_price = entry
    settle_actual_date, settle_price = settle
    if entry_price <= 0:
        return None
    pnl_pct = (settle_price - entry_price) / entry_price
    return VetoRegret(
        symbol=symbol.upper(),
        entry_date=entry_date.isoformat(),
        settle_date=settle_actual_date.isoformat(),
        estimated_entry_price=round(entry_price, 4),
        actual_price_at_settle=round(settle_price, 4),
        counterfactual_pnl_pct=round(pnl_pct, 6),
        holding_window_days=holding_window_days,
    )


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------


# Tests monkey-patch this. Production code calls `default_price_provider()`.
_PROVIDER_FACTORY: Callable[[], PriceProvider] | None = None


def set_provider_factory(factory: Callable[[], PriceProvider] | None) -> None:
    global _PROVIDER_FACTORY
    _PROVIDER_FACTORY = factory


def default_price_provider() -> PriceProvider:
    if _PROVIDER_FACTORY is not None:
        return _PROVIDER_FACTORY()
    return QlibPriceProvider()
