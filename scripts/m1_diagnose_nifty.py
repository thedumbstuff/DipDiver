"""Diagnose why the NIFTY 50 baseline returned -22% absolute.

Checks four hypotheses, in order:

1. Benchmark sanity: does the NSEI cum return in our store match Yahoo?
2. Universe-wide test-period return — were Indian stocks actually up in 2024-2025?
3. TMPV.NS history truncation — does it pollute the training cross-section?
4. Per-ticker factor/price sanity — are any tickers obviously wrong vs Yahoo?

Run from the repo root with the brain venv active:
    .venv/Scripts/python.exe scripts/m1_diagnose_nifty.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

import qlib
from qlib.data import D

from dipdiver._paths import resolve_provider_uri
from dipdiver.brain.baselines.universes import NIFTY50

TRAIN_START = "2014-01-01"
TEST_START = "2024-01-01"
TEST_END = "2025-12-31"


def init_qlib() -> None:
    qlib.init(
        provider_uri=str(resolve_provider_uri("data/qlib/in_data")),
        region="us",
    )


def cum_return(series: pd.Series) -> float:
    series = series.dropna()
    if len(series) < 2:
        return float("nan")
    return float(series.iloc[-1] / series.iloc[0] - 1)


def read_store(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Read close + factor for one symbol from our Qlib store."""
    df = D.features(
        instruments=[symbol.lower()],
        fields=["$close", "$factor"],
        start_time=start,
        end_time=end,
        freq="day",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["close", "factor"])
    df = df.droplevel(0)  # drop instrument level
    df.columns = ["close", "factor"]
    return df


def fetch_yahoo(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Fetch close + adj_close fresh from Yahoo for comparison."""
    df = yf.download(
        symbol, start=start, end=end, auto_adjust=False, progress=False, threads=False
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["close", "adj_close"])
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.rename(columns={"Close": "close", "Adj Close": "adj_close"})[
        ["close", "adj_close"]
    ]


# ---------------------------------------------------------------------------
# Check 1 — benchmark
# ---------------------------------------------------------------------------


def check_benchmark() -> None:
    print("\n=== Check 1: benchmark NSEI ===")
    store = read_store("nsei", TEST_START, TEST_END)
    yf_df = fetch_yahoo("^NSEI", TEST_START, TEST_END)
    if store.empty:
        print("  store: NO DATA — benchmark missing from in_data store")
        return
    if yf_df.empty:
        print("  yahoo: NO DATA — ^NSEI not reachable")
        return
    # In our format, $close is already adjusted (factor folded in by data handler).
    # Compare adjusted price trajectories.
    store_cum = cum_return(store["close"])
    yf_cum = cum_return(yf_df["adj_close"])
    print(f"  rows: store={len(store)}, yahoo={len(yf_df)}")
    print(f"  cum return test window: store={store_cum:+.2%}  yahoo(adj)={yf_cum:+.2%}")
    if abs(store_cum - yf_cum) > 0.10:
        print("  ALERT: store benchmark return diverges from Yahoo by >10pp")
    else:
        print("  ok: matches within 10pp")


# ---------------------------------------------------------------------------
# Check 2 — universe-wide returns
# ---------------------------------------------------------------------------


def check_universe_returns() -> None:
    print("\n=== Check 2: universe-wide test-period return ===")
    returns: list[tuple[str, float, int]] = []
    for tic in NIFTY50.instruments:
        store = read_store(tic, TEST_START, TEST_END)
        if store.empty:
            continue
        r = cum_return(store["close"])
        returns.append((tic, r, len(store)))
    if not returns:
        print("  no data!")
        return
    rets = pd.Series({t: r for t, r, _ in returns})
    print(f"  n_tickers: {len(returns)}")
    print(f"  median return: {rets.median():+.2%}")
    print(f"  mean return:   {rets.mean():+.2%}")
    print(f"  pct positive:  {(rets > 0).mean():.1%}")
    print(f"  worst 5:       {rets.nsmallest(5).to_dict()}")
    print(f"  best 5:        {rets.nlargest(5).to_dict()}")


# ---------------------------------------------------------------------------
# Check 3 — TMPV
# ---------------------------------------------------------------------------


def check_tmpv() -> None:
    print("\n=== Check 3: TMPV.NS history ===")
    full_train = read_store("tmpv.ns", TRAIN_START, TEST_END)
    if full_train.empty:
        print("  TMPV.NS has NO data in store — would be dropped by Qlib")
        return
    train_only = read_store("tmpv.ns", TRAIN_START, "2022-12-31")
    test_only = read_store("tmpv.ns", TEST_START, TEST_END)
    print(f"  total rows: {len(full_train)}, first date: {full_train.index.min().date()}")
    print(f"  rows in train window (2014-2022): {len(train_only)}")
    print(f"  rows in test window  (2024-2025): {len(test_only)}")
    if len(train_only) < 50:
        print("  ALERT: TMPV has <50 training rows — will be effectively absent during fit")


# ---------------------------------------------------------------------------
# Check 4 — per-ticker factor sanity
# ---------------------------------------------------------------------------


def check_factors() -> None:
    print("\n=== Check 4: per-ticker factor mean/stdev (test window) ===")
    print("  factor = adj_close/close in Yahoo terms. Mean far from 1 hints at")
    print("  split/bonus adjustments concentrated in our window.")
    rows = []
    for tic in NIFTY50.instruments[:15]:  # sample
        store = read_store(tic, TEST_START, TEST_END)
        if store.empty:
            continue
        f = store["factor"].dropna()
        rows.append(
            (tic, float(f.mean()), float(f.std()), float(f.iloc[0]), float(f.iloc[-1]))
        )
    if not rows:
        print("  no data!")
        return
    df = pd.DataFrame(rows, columns=["ticker", "mean", "stdev", "first", "last"])
    print(df.to_string(index=False))
    suspicious = df[(df["stdev"] > 0.05) | (abs(df["mean"] - 1.0) > 0.5)]
    if not suspicious.empty:
        print("\n  ALERT: suspicious factor behaviour on:")
        print(suspicious.to_string(index=False))


# ---------------------------------------------------------------------------
# Check 5 — one ticker round-trip: store vs fresh Yahoo
# ---------------------------------------------------------------------------


def check_roundtrip(sample: str = "RELIANCE.NS") -> None:
    print(f"\n=== Check 5: round-trip on {sample} (test window) ===")
    store = read_store(sample, TEST_START, TEST_END)
    yf_df = fetch_yahoo(sample, TEST_START, TEST_END)
    if store.empty or yf_df.empty:
        print("  one side empty; cannot compare")
        return
    store_cum = cum_return(store["close"])  # this is already adjusted
    yf_close_cum = cum_return(yf_df["close"])  # raw close — would not match
    yf_adj_cum = cum_return(yf_df["adj_close"])  # adjusted — should match
    print(f"  store rows={len(store)}  yahoo rows={len(yf_df)}")
    print(f"  store adj-close cum:   {store_cum:+.2%}")
    print(f"  yahoo raw-close cum:   {yf_close_cum:+.2%}")
    print(f"  yahoo adj-close cum:   {yf_adj_cum:+.2%}")
    delta = store_cum - yf_adj_cum
    print(f"  store vs yahoo(adj):   {delta:+.2%}")
    if abs(delta) > 0.05:
        print("  ALERT: store diverges from Yahoo adjusted by >5pp")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    init_qlib()
    check_benchmark()
    check_universe_returns()
    check_tmpv()
    check_factors()
    check_roundtrip("RELIANCE.NS")
    check_roundtrip("ICICIBANK.NS")
    check_roundtrip("INFY.NS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
