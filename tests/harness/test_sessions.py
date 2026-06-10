"""Exchange-session resolution + entry/exit timing math.

Fixed `now_utc` datetimes throughout — June 2026, so US exchanges are on
DST (EDT = UTC-4). 2026-06-10 is a Wednesday, 2026-06-13 a Saturday.
"""

from __future__ import annotations

from datetime import datetime, timezone

from dipdiver.harness.sessions import (
    entry_exit_for,
    next_trading_session,
    session_for,
)


WED_MORNING_UTC = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)   # 08:00 EDT
FRI_EVENING_UTC = datetime(2026, 6, 12, 21, 0, tzinfo=timezone.utc)   # 17:00 EDT
SATURDAY_UTC = datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# session_for
# ---------------------------------------------------------------------------


def test_us_universes_resolve_to_nyse():
    for u in ("dow30", "sp500"):
        s = session_for(u)
        assert s.exchange == "NYSE"
        assert s.tz == "America/New_York"


def test_nifty50_resolves_to_nse_india():
    s = session_for("nifty50", "RELIANCE.NS")
    assert s.tz == "Asia/Kolkata"
    assert s.open_time.hour == 9 and s.open_time.minute == 15
    assert s.close_time.hour == 15 and s.close_time.minute == 30


def test_world_indices_resolve_per_symbol():
    assert session_for("world_indices", "^N225").tz == "Asia/Tokyo"
    assert session_for("world_indices", "^FTSE").tz == "Europe/London"
    assert session_for("world_indices", "000001.SS").tz == "Asia/Shanghai"
    assert session_for("world_indices", "^NSEI").tz == "Asia/Kolkata"


def test_unknown_symbol_and_universe_fall_back_to_nyse():
    assert session_for("world_indices", "^NOPE").exchange == "NYSE"
    assert session_for("not_a_universe").exchange == "NYSE"


def test_crypto_is_continuous():
    s = session_for("crypto", "BTC-USD")
    assert s.continuous is True
    assert 5 in s.days and 6 in s.days  # trades on weekends


# ---------------------------------------------------------------------------
# next_trading_session
# ---------------------------------------------------------------------------


def test_weekday_before_cutoff_is_today():
    s = session_for("dow30")
    assert next_trading_session(s, WED_MORNING_UTC).isoformat() == "2026-06-10"


def test_friday_after_close_rolls_to_monday():
    s = session_for("dow30")
    assert next_trading_session(s, FRI_EVENING_UTC).isoformat() == "2026-06-15"


def test_saturday_rolls_to_monday():
    s = session_for("dow30")
    assert next_trading_session(s, SATURDAY_UTC).isoformat() == "2026-06-15"


def test_nse_already_closed_rolls_to_next_day():
    # 12:00 UTC = 17:30 IST — NSE closed at 15:30, so Thursday is next.
    s = session_for("nifty50")
    assert next_trading_session(s, WED_MORNING_UTC).isoformat() == "2026-06-11"


def test_crypto_session_is_always_today():
    s = session_for("crypto")
    assert next_trading_session(s, SATURDAY_UTC).isoformat() == "2026-06-13"


# ---------------------------------------------------------------------------
# entry_exit_for
# ---------------------------------------------------------------------------


def test_dow30_entry_exit_times():
    t = entry_exit_for("dow30", "AAPL", now_utc=WED_MORNING_UTC)
    assert t.session_date == "2026-06-10"
    assert t.entry_local == "09:45"   # open 09:30 + 15
    assert t.exit_local == "15:45"    # close 16:00 - 15
    assert t.entry_utc == "13:45"     # EDT = UTC-4
    assert t.exit_utc == "19:45"
    assert t.is_open_now is False     # 08:00 EDT, pre-open


def test_dow30_is_open_now_during_session():
    during = datetime(2026, 6, 10, 15, 0, tzinfo=timezone.utc)  # 11:00 EDT
    t = entry_exit_for("dow30", "AAPL", now_utc=during)
    assert t.is_open_now is True


def test_nifty50_entry_exit_in_ist():
    t = entry_exit_for("nifty50", "RELIANCE.NS", now_utc=WED_MORNING_UTC)
    assert t.session_date == "2026-06-11"  # 17:30 IST → today's session spent
    assert t.entry_local == "09:30"  # open 09:15 + 15
    assert t.exit_local == "15:15"   # close 15:30 - 15
    assert t.entry_utc == "04:00"    # IST = UTC+5:30
    assert t.exit_utc == "09:45"


def test_world_index_uses_home_exchange():
    t = entry_exit_for("world_indices", "^N225", now_utc=SATURDAY_UTC)
    assert t.tz_name == "Asia/Tokyo"
    assert t.entry_local == "09:15"
    assert t.exit_local == "15:15"
    assert t.session_date == "2026-06-15"  # Monday


def test_crypto_guidance_carries_note():
    t = entry_exit_for("crypto", "BTC-USD", now_utc=SATURDAY_UTC)
    assert t.is_open_now is True
    assert t.note is not None and "24/7" in t.note


def test_naive_datetime_treated_as_utc():
    t = entry_exit_for("dow30", "AAPL", now_utc=datetime(2026, 6, 10, 12, 0))
    assert t.session_date == "2026-06-10"
