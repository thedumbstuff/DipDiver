"""Stage 7 / M14 — benchmark P&L lookup for excess-return reporting.

Reads (or caches) a benchmark daily-close series and exposes a helper that
returns the % change between two dates. Strategy-vs-benchmark excess return
is computed inside `render_full_report` via this module.

Production source: yfinance pulled once a day and cached at
`data/benchmarks/<symbol>.csv` (date,close). When the CSV is missing or yfinance
is unavailable, lookups return None and the scoreboard falls back to the
strategy-only view — no crashes.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path

from dipdiver._paths import repo_root

log = logging.getLogger(__name__)


_CACHE: dict[str, dict[str, float]] = {}


@dataclass(frozen=True)
class BenchmarkPoint:
    date: str
    close: float


def benchmark_csv_path(symbol: str) -> Path:
    return repo_root() / "data" / "benchmarks" / f"{symbol}.csv"


def load_series(symbol: str) -> dict[str, float]:
    """Load `{date: close}` from CSV cache. Returns {} when missing."""
    if symbol in _CACHE:
        return _CACHE[symbol]
    p = benchmark_csv_path(symbol)
    if not p.exists():
        _CACHE[symbol] = {}
        return {}
    out: dict[str, float] = {}
    with p.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "").strip()
            try:
                c = float(row.get("close") or "nan")
            except ValueError:
                continue
            if d and c == c:
                out[d] = c
    _CACHE[symbol] = out
    return out


def reset_cache() -> None:
    _CACHE.clear()


def close_on_or_before(series: dict[str, float], target_date: date_cls) -> float | None:
    """Walk back up to 7 days to the most recent available close."""
    for offset in range(0, 8):
        probe = (target_date - timedelta(days=offset)).isoformat()
        if probe in series:
            return series[probe]
    return None


def daily_excess_pct(
    *, symbol: str, target_date: date_cls,
) -> float | None:
    """Return the benchmark's day-over-day % change ending at target_date.

    Used to compute (strategy_pnl_pct - benchmark_pnl_pct) per day.
    """
    series = load_series(symbol)
    if not series:
        return None
    today_close = close_on_or_before(series, target_date)
    prior_close = close_on_or_before(series, target_date - timedelta(days=1))
    if today_close is None or prior_close in (None, 0):
        return None
    return (today_close - prior_close) / prior_close
