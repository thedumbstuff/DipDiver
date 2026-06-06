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
    region: str  # "us" | "in" | "crypto" | "world"
    instruments: tuple[str, ...]
    benchmark: str            # in-store symbol name used by Qlib (e.g. "DJI")
    benchmark_yahoo: str      # Yahoo symbol used to fetch (e.g. "^DJI")
    # Stage 6 / M13 — whether this universe can execute live via the current
    # broker adapters. world_indices / crypto / nifty50 are research-only on
    # Alpaca; flagging at the source documents intent independently of the
    # SUPPORTED_UNIVERSES set on the adapter.
    live_executable: bool = True

    def __len__(self) -> int:
        return len(self.instruments)

    @property
    def symbols(self) -> tuple[str, ...]:
        """Alias for instruments — used by registry_api for consistency."""
        return self.instruments

    @property
    def label(self) -> str:
        return self.name


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
    # Indian markets via Zerodha not yet wired — Alpaca cannot execute these.
    # Flip to True when the IBKR or Zerodha adapter is ready.
    live_executable=False,
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
    live_executable=False,
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
    live_executable=False,
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


# Stage 6 / M13 — SP500 starter membership. Use only DOW30 ∪ top-N S&P 500
# constituents to keep the install footprint small. For a precise 500-name
# snapshot, expand via `data/universes/sp500.csv` (one ticker per line) which
# `data_load_sp500()` reads when present.
_SP500_STARTER = (
    # Tech / megacap
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "META", "AMZN", "TSLA", "AVGO", "ORCL",
    "ADBE", "CRM", "NFLX", "AMD", "INTC", "CSCO", "QCOM", "TXN", "IBM", "INTU",
    # Financials
    "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "BLK",
    # Healthcare
    "JNJ", "UNH", "PFE", "ABBV", "LLY", "MRK", "TMO", "ABT", "DHR", "BMY",
    # Consumer / industrials / energy
    "WMT", "PG", "KO", "PEP", "MCD", "COST", "HD", "DIS", "NKE", "SBUX",
    "XOM", "CVX", "BA", "CAT", "GE", "HON", "MMM", "UPS", "FDX", "LMT",
)


def _load_sp500_extension() -> tuple[str, ...]:
    """Optionally extend SP500 with extra tickers from data/universes/sp500.csv.

    Allows ops to refresh membership without touching code. Missing file is
    fine — the starter list is enough to train and trade on.
    """
    try:
        from dipdiver._paths import repo_root
        path = repo_root() / "data" / "universes" / "sp500.csv"
        if not path.exists():
            return ()
        out: list[str] = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            t = raw.strip().upper()
            if not t or t.startswith("#"):
                continue
            out.append(t)
        return tuple(out)
    except Exception:  # noqa: BLE001
        return ()


_sp500_full = tuple(sorted(set(_SP500_STARTER + _load_sp500_extension())))


SP500 = Universe(
    name="sp500",
    region="us",
    instruments=_sp500_full,
    benchmark="GSPC",
    benchmark_yahoo="^GSPC",
    live_executable=True,
)


UNIVERSES: dict[str, Universe] = {
    u.name: u for u in (DOW30, SP500, NIFTY50, CRYPTO_BASKET, WORLD_INDICES)
}


def get_universe(name: str) -> Universe:
    try:
        return UNIVERSES[name]
    except KeyError as e:
        known = ", ".join(sorted(UNIVERSES))
        raise ValueError(f"unknown universe {name!r}; known: {known}") from e
