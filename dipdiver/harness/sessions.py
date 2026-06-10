"""Exchange-session metadata + day-trading entry/exit timing.

The signal CSVs carry *which* symbol to pick; this module answers *when* —
the next actionable trading session for the symbol's exchange, a suggested
entry time (after the opening auction volatility settles) and a suggested
exit time (before the close, so no overnight risk is carried).

Scope notes:
- Weekend skipping only. Exchange holidays are NOT modeled — on a holiday
  the suggested session will be wrong by one day. The UI labels times as
  "suggested", and the universe's local calendar is authoritative.
- Lunch breaks (TSE, HKEX, SSE) are ignored: entry/exit anchor on the full
  session open/close, which is what a once-a-day dip strategy needs.
- Crypto trades 24/7; its "session" is the UTC day and timing guidance is
  advisory only (flagged `continuous=True`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


# Minutes after the open before entering — lets the opening auction /
# first-print volatility settle before acting on a daily signal.
ENTRY_DELAY_MIN = 15

# Minutes before the close to be flat — avoids the closing-auction scramble
# and guarantees no overnight gap risk for a day-trade.
EXIT_BUFFER_MIN = 15

_WEEKDAYS = (0, 1, 2, 3, 4)  # Mon..Fri
_ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)


@dataclass(frozen=True)
class ExchangeSession:
    exchange: str          # display label, e.g. "NYSE"
    tz: str                # IANA zone, e.g. "America/New_York"
    open_time: time
    close_time: time
    days: tuple[int, ...] = _WEEKDAYS
    continuous: bool = False  # 24/7 market (crypto)


@dataclass(frozen=True)
class TimingGuidance:
    """Entry/exit suggestion for one pick on its next trading session."""

    exchange: str
    tz_name: str           # IANA zone
    session_date: str      # YYYY-MM-DD in exchange-local terms
    entry_local: str       # "09:45"
    exit_local: str        # "15:45"
    entry_utc: str         # "13:45"
    exit_utc: str          # "19:45"
    is_open_now: bool
    note: str | None = None


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------


NYSE = ExchangeSession("NYSE", "America/New_York", time(9, 30), time(16, 0))
NSE_INDIA = ExchangeSession("NSE", "Asia/Kolkata", time(9, 15), time(15, 30))
CRYPTO_24_7 = ExchangeSession(
    "Crypto (24/7)", "UTC", time(0, 0), time(23, 59),
    days=_ALL_DAYS, continuous=True,
)

# Universe-level defaults. world_indices is per-symbol (see below).
_UNIVERSE_SESSIONS: dict[str, ExchangeSession] = {
    "dow30": NYSE,
    "sp500": NYSE,
    "nifty50": NSE_INDIA,
    "crypto": CRYPTO_24_7,
}

# world_indices: each index trades on its home exchange.
_WORLD_INDEX_SESSIONS: dict[str, ExchangeSession] = {
    "^GSPTSE": ExchangeSession("TSX", "America/Toronto", time(9, 30), time(16, 0)),
    "^FTSE": ExchangeSession("LSE", "Europe/London", time(8, 0), time(16, 30)),
    "^GDAXI": ExchangeSession("XETRA", "Europe/Berlin", time(9, 0), time(17, 30)),
    "^FCHI": ExchangeSession("Euronext Paris", "Europe/Paris", time(9, 0), time(17, 30)),
    "^STOXX50E": ExchangeSession("Eurex", "Europe/Berlin", time(9, 0), time(17, 30)),
    "^N225": ExchangeSession("TSE", "Asia/Tokyo", time(9, 0), time(15, 30)),
    "^HSI": ExchangeSession("HKEX", "Asia/Hong_Kong", time(9, 30), time(16, 0)),
    "^AXJO": ExchangeSession("ASX", "Australia/Sydney", time(10, 0), time(16, 0)),
    "^NSEI": NSE_INDIA,
    "^BVSP": ExchangeSession("B3", "America/Sao_Paulo", time(10, 0), time(17, 0)),
    "^MXX": ExchangeSession("BMV", "America/Mexico_City", time(8, 30), time(15, 0)),
    "^KS11": ExchangeSession("KRX", "Asia/Seoul", time(9, 0), time(15, 30)),
    "^TWII": ExchangeSession("TWSE", "Asia/Taipei", time(9, 0), time(13, 30)),
    "000001.SS": ExchangeSession("SSE", "Asia/Shanghai", time(9, 30), time(15, 0)),
}


def session_for(universe: str, symbol: str | None = None) -> ExchangeSession:
    """Resolve the trading session for a (universe, symbol) pair.

    world_indices resolves per symbol; everything else by universe. Unknown
    universes fall back to NYSE — wrong is better than crashing here, and the
    guidance is labeled "suggested" in the UI.
    """
    if universe == "world_indices" and symbol:
        s = _WORLD_INDEX_SESSIONS.get(symbol.strip().upper())
        if s is not None:
            return s
    return _UNIVERSE_SESSIONS.get(universe, NYSE)


# ---------------------------------------------------------------------------
# Timing math
# ---------------------------------------------------------------------------


def next_trading_session(session: ExchangeSession, now_utc: datetime) -> date:
    """The next actionable session date, in exchange-local terms.

    "Actionable" = a trading day where the user can still both enter and
    exit: if local now is already past (close − EXIT_BUFFER), today's
    session is spent and we roll to the next trading day. Weekends are
    skipped; exchange holidays are not modeled.
    """
    tz = ZoneInfo(session.tz)
    local_now = now_utc.astimezone(tz)
    d = local_now.date()
    cutoff = _minus_minutes(session.close_time, EXIT_BUFFER_MIN)
    if session.continuous:
        return d
    if local_now.weekday() in session.days and local_now.time() < cutoff:
        return d
    d += timedelta(days=1)
    while d.weekday() not in session.days:
        d += timedelta(days=1)
    return d


def _minus_minutes(t: time, minutes: int) -> time:
    dt = datetime.combine(date(2000, 1, 1), t) - timedelta(minutes=minutes)
    return dt.time()


def _plus_minutes(t: time, minutes: int) -> time:
    dt = datetime.combine(date(2000, 1, 1), t) + timedelta(minutes=minutes)
    return dt.time()


def entry_exit_for(
    universe: str,
    symbol: str | None = None,
    *,
    now_utc: datetime | None = None,
) -> TimingGuidance:
    """Suggested entry/exit times for the next actionable session.

    Entry = open + ENTRY_DELAY_MIN; exit = close − EXIT_BUFFER_MIN, both in
    the exchange's local timezone, with UTC equivalents for cross-checking.
    """
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    session = session_for(universe, symbol)
    tz = ZoneInfo(session.tz)
    session_date = next_trading_session(session, now_utc)

    entry_t = _plus_minutes(session.open_time, ENTRY_DELAY_MIN)
    exit_t = _minus_minutes(session.close_time, EXIT_BUFFER_MIN)
    entry_local_dt = datetime.combine(session_date, entry_t, tzinfo=tz)
    exit_local_dt = datetime.combine(session_date, exit_t, tzinfo=tz)

    local_now = now_utc.astimezone(tz)
    is_open_now = (
        session.continuous
        or (
            local_now.weekday() in session.days
            and session.open_time <= local_now.time() < session.close_time
        )
    )

    note = None
    if session.continuous:
        note = (
            "24/7 market — no exchange session. Times anchor on the UTC day "
            "the daily signal closes; enter/exit at your discretion."
        )

    return TimingGuidance(
        exchange=session.exchange,
        tz_name=session.tz,
        session_date=session_date.isoformat(),
        entry_local=entry_local_dt.strftime("%H:%M"),
        exit_local=exit_local_dt.strftime("%H:%M"),
        entry_utc=entry_local_dt.astimezone(timezone.utc).strftime("%H:%M"),
        exit_utc=exit_local_dt.astimezone(timezone.utc).strftime("%H:%M"),
        is_open_now=is_open_now,
        note=note,
    )
