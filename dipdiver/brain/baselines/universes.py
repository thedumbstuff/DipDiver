"""Tradable universes for M1 baselines.

Point-in-time membership is enforced at backtest time by the data layer — these
lists are the *current* membership and should be refreshed before any new lock.
The point-in-time history lives next to the Qlib data snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Universe:
    name: str
    region: str  # "us" | "in" | "crypto"
    instruments: tuple[str, ...]
    benchmark: str            # in-store symbol name used by Qlib (e.g. "DJI")
    benchmark_yahoo: str      # Yahoo symbol used to fetch (e.g. "^DJI")

    def __len__(self) -> int:
        return len(self.instruments)


# Dow Jones Industrial Average — 30 components.
# Refresh from S&P / WSJ before each new lock; membership drifts.
DOW30 = Universe(
    name="dow30",
    region="us",
    instruments=(
        "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
        "GS", "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK",
        "MSFT", "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT",
    ),
    benchmark="DJI",
    benchmark_yahoo="^DJI",
)

# NIFTY 50 — NSE India.
# Tickers are Yahoo-style with the .NS suffix; the Qlib instrument adapter
# strips/translates as needed when loading from the Qlib store.
NIFTY50 = Universe(
    name="nifty50",
    region="in",
    instruments=(
        "ADANIENT.NS", "ADANIPORTS.NS", "APOLLOHOSP.NS", "ASIANPAINT.NS", "AXISBANK.NS",
        "BAJAJ-AUTO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS", "BEL.NS", "BHARTIARTL.NS",
        "CIPLA.NS", "COALINDIA.NS", "DRREDDY.NS", "EICHERMOT.NS", "GRASIM.NS",
        "HCLTECH.NS", "HDFCBANK.NS", "HDFCLIFE.NS", "HEROMOTOCO.NS", "HINDALCO.NS",
        "HINDUNILVR.NS", "ICICIBANK.NS", "INDUSINDBK.NS", "INFY.NS", "ITC.NS",
        "JSWSTEEL.NS", "KOTAKBANK.NS", "LT.NS", "M&M.NS", "MARUTI.NS",
        "NESTLEIND.NS", "NTPC.NS", "ONGC.NS", "POWERGRID.NS", "RELIANCE.NS",
        "SBILIFE.NS", "SBIN.NS", "SHRIRAMFIN.NS", "SUNPHARMA.NS", "TATACONSUM.NS",
        # TATAMOTORS demerger effective 2025-10-01: legacy entity (PV + JLR + EV)
        # renamed to TMPV; the new TATAMOTORS holds only the commercial-vehicle
        # business with no pre-Oct-2025 price history. Use TMPV for continuity.
        "TMPV.NS", "TATASTEEL.NS", "TCS.NS", "TECHM.NS", "TITAN.NS",
        # ZOMATO rebranded to ETERNAL (NSE, March 2025).
        "TRENT.NS", "ULTRACEMCO.NS", "UPL.NS", "WIPRO.NS", "ETERNAL.NS",
    ),
    benchmark="NSEI",
    benchmark_yahoo="^NSEI",
)

# Small crypto basket — spot only, USD-quoted.
CRYPTO_BASKET = Universe(
    name="crypto",
    region="crypto",
    instruments=("BTC-USD", "ETH-USD", "SOL-USD"),
    benchmark="BTC-USD",        # already in the universe
    benchmark_yahoo="BTC-USD",
)


# 15 major country indices, USD or local-currency closes from Yahoo.
# Cross-country diversified — North America, Europe, developed + emerging Asia, LATAM, Oceania.
# Index price returns do not include dividends; for cross-sectional ranking this is fine
# since the bias is uniform across the universe. Benchmark is S&P 500.
WORLD_INDICES = Universe(
    name="world_indices",
    region="world",
    # 14 non-US country indices. S&P 500 is the benchmark only — the baseline
    # question is "can a global cross-country rotation beat the US?".
    instruments=(
        "^GSPTSE",    # Canada — TSX Composite
        "^FTSE",      # UK — FTSE 100
        "^GDAXI",     # Germany — DAX
        "^FCHI",      # France — CAC 40
        "^STOXX50E",  # Eurozone — Euro Stoxx 50
        "^N225",      # Japan — Nikkei 225
        "^HSI",       # Hong Kong — Hang Seng
        "^AXJO",      # Australia — ASX 200
        "^NSEI",      # India — Nifty 50
        "^BVSP",      # Brazil — Bovespa
        "^MXX",       # Mexico — IPC
        "^KS11",      # South Korea — KOSPI
        "^TWII",      # Taiwan — TAIEX
        "000001.SS",  # China — Shanghai Composite
    ),
    benchmark="GSPC",
    benchmark_yahoo="^GSPC",
)


UNIVERSES: dict[str, Universe] = {
    u.name: u for u in (DOW30, NIFTY50, CRYPTO_BASKET, WORLD_INDICES)
}


def get_universe(name: str) -> Universe:
    try:
        return UNIVERSES[name]
    except KeyError as e:
        known = ", ".join(sorted(UNIVERSES))
        raise ValueError(f"unknown universe {name!r}; known: {known}") from e
