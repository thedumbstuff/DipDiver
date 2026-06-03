# region imports
from AlgorithmImports import *
import csv
import os
from collections import defaultdict
# endregion


# Resolve project-root-relative paths inside Lean's docker container.
# Lean mounts the project at /LeanCLI/ and runs main.py from there;
# __file__ here is /LeanCLI/main.py, so PROJECT_DIR == /LeanCLI/.
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# DipDiver M3 - DOW 30 LightGBM strategy executed through Lean.
#
# This is the Lean side of the M1 <-> M3 parity test. It consumes a pre-computed
# signal CSV produced by `scripts/m3_export_signals.py` and rebalances daily
# top-k / n-drop, matching M1's qlib backtest configuration.
#
# Constants MUST mirror dipdiver/brain/baselines/configs/dow30_lightgbm.yaml.
# ---------------------------------------------------------------------------


# DOW 30 instruments - must match dipdiver.brain.baselines.universes.DOW30.
# We hardcode here because Lean's docker container does NOT have the dipdiver
# package installed; only Lean + standard scientific Python.
DOW30_SYMBOLS = (
    "AAPL", "AMGN", "AMZN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS",
    "GS",   "HD",   "HON",  "IBM", "JNJ", "JPM", "KO",  "MCD",  "MMM", "MRK",
    "MSFT", "NKE",  "NVDA", "PG",  "SHW", "TRV", "UNH", "V",    "VZ",  "WMT",
)

# Strategy params - must match the M1 YAML's backtest_params block.
TOPK = 10
N_DROP = 3
OPEN_COST = 0.0001   #  1 bp (Alpaca-style retail commission baseline)
CLOSE_COST = 0.0002  #  2 bps
INITIAL_CASH = 100_000_000  # matches qlib port_analysis_config.backtest.account

# Test window - matches M1 YAML's test_start / test_end.
TEST_START_Y, TEST_START_M, TEST_START_D = 2024, 1, 1
TEST_END_Y,   TEST_END_M,   TEST_END_D   = 2025, 12, 31

# Filename of the signal CSV (project data/ folder). Generated offline by
# scripts/m3_export_signals.py. Format: date,symbol,score (no header surprises).
SIGNAL_CSV_FILENAME = "signals.csv"


class DipDiverFeeModel(FeeModel):
    """Percentage-of-notional fee, to mirror Qlib's open_cost / close_cost.

    Lean's default brokerage fees are per-share, which doesn't translate
    cleanly to Qlib's percent-of-trade cost model. This custom model gives
    a like-for-like comparison.
    """

    def get_order_fee(self, parameters):
        order = parameters.order
        price = parameters.security.price
        notional = abs(order.quantity) * price
        rate = OPEN_COST if order.direction == OrderDirection.BUY else CLOSE_COST
        return OrderFee(CashAmount(notional * rate, "USD"))


class DipDiverDow30LightGBM(QCAlgorithm):
    """Top-k / n-drop daily rebalancer consuming a pre-computed signal CSV."""

    def initialize(self) -> None:
        self.set_start_date(TEST_START_Y, TEST_START_M, TEST_START_D)
        self.set_end_date(TEST_END_Y, TEST_END_M, TEST_END_D)
        self.set_cash(INITIAL_CASH)

        # Daily bars only - matches Qlib's backtest resolution.
        self._symbol_by_ticker: dict[str, Symbol] = {}
        for tic in DOW30_SYMBOLS:
            eq = self.add_equity(tic, Resolution.DAILY)
            eq.set_fee_model(DipDiverFeeModel())
            self._symbol_by_ticker[tic] = eq.symbol

        # Benchmark: omitted for now. The DIA ETF isn't in our local data
        # store (we have ^DJI under a custom name) so Lean would fail to
        # download benchmark bars. Acceptable: we compare M1 vs Lean via
        # the parity script, not via Lean's built-in benchmark chart.

        # Pre-load all signals indexed by date.
        self._signals_by_date: dict[str, list] = self._load_signals()
        self.log(f"DipDiver: loaded signals for {len(self._signals_by_date)} dates "
                 f"({sum(len(v) for v in self._signals_by_date.values())} total rows)")

        # Rebalance shortly after market open, using yesterday's signal.
        # With daily-resolution data, Lean fills market orders at the next
        # bar's open regardless of the call time, so the open schedule with
        # signal_{t-1} effectively trades at open(t) ~= close(t-1), exactly
        # the price the signal was computed against. This gives the closest
        # empirical parity with M1 (close-of-day variant was worse).
        self.schedule.on(
            self.date_rules.every_day(self._symbol_by_ticker["AAPL"]),
            self.time_rules.after_market_open(self._symbol_by_ticker["AAPL"], 1),
            self._rebalance,
        )

        # Track current holdings so we know what to liquidate on rotation.
        self._current_holdings: set = set()
        # Cached sorted list of past signal dates, for "previous trading day"
        # lookup at rebalance time.
        self._sorted_signal_dates: list = sorted(self._signals_by_date.keys())

    # ------------------------------------------------------------------
    # Signal IO
    # ------------------------------------------------------------------

    def _load_signals(self) -> dict:
        """Read signals.csv from the project data/ dir.

        Lean's local CLI mounts the project directory into the engine
        container; relative path 'signals.csv' resolves under the project
        root. If this errors with "file not found", the most likely cause
        is that signals.csv wasn't copied into the project - see
        docs/milestones/M3_lean.md step 5.
        """
        signals_by_date = defaultdict(list)
        path = os.path.join(PROJECT_DIR, SIGNAL_CSV_FILENAME)
        try:
            with open(path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    date = row["date"].strip()
                    # Qlib stores symbols lowercase ('aapl'); Lean expects 'AAPL'.
                    symbol = row["symbol"].strip().upper()
                    score = float(row["score"])
                    if score != score:  # NaN
                        continue
                    if symbol in self._symbol_by_ticker:
                        signals_by_date[date].append((symbol, score))
            return dict(signals_by_date)
        except FileNotFoundError:
            self.error(
                f"signals.csv not found at {path}. Copy it with: "
                f"cp data/signals/dow30_lightgbm.csv "
                f"lean_projects/dipdiver_dow30_lightgbm/signals.csv"
            )
            return {}

    def _previous_signal_date(self, today: str):
        """Largest signal date strictly less than today (an ISO date string)."""
        import bisect

        idx = bisect.bisect_left(self._sorted_signal_dates, today)
        if idx == 0:
            return None
        return self._sorted_signal_dates[idx - 1]

    # ------------------------------------------------------------------
    # Rebalance
    # ------------------------------------------------------------------

    def _rebalance(self) -> None:
        """Implement Qlib's TopkDropoutStrategy semantics.

        Rules each rebalance day:
          1. If no holdings: open top-K by score.
          2. Otherwise: drop the bottom-N_DROP of CURRENT HOLDINGS (ranked by
             today's score), add the top-N_DROP of NON-HELD names. Carry the
             remaining K-N_DROP holdings unchanged.

        This is much lazier than picking today's top-K: at most N_DROP names
        rotate per day. Without this, naive top-K creates 2-3x the trading
        and breaks parity with M1.
        """
        # Qlib's label is  Ref($close, -2) / Ref($close, -1) - 1 = the return
        # from close(t+1) to close(t+2). So signal_{t-1} (computed at close of
        # t-1) drives the trade that captures close(t) -> close(t+1).
        # Therefore at close of day d, we use signal dated d-1, NOT d.
        # Using today's signal at today's close trades on the wrong window
        # and inverts the strategy (Sharpe goes negative).
        today_iso = self.time.strftime("%Y-%m-%d")
        signal_date = self._previous_signal_date(today_iso)
        if signal_date is None:
            return
        signals = self._signals_by_date.get(signal_date)
        if not signals:
            return

        # Sort by score, descending; build a score lookup for fast rerank.
        signals_sorted = sorted(signals, key=lambda kv: kv[1], reverse=True)
        scores = {sym: score for sym, score in signals_sorted}

        if not self._current_holdings:
            # First rebalance — open the top-K.
            target_tickers = {sym for sym, _ in signals_sorted[:TOPK]}
        else:
            # TopkDropoutStrategy: drop bottom-N_DROP of held + add top-N_DROP of non-held.
            current_ranked = sorted(
                self._current_holdings,
                key=lambda t: scores.get(t, float("-inf")),
            )
            to_drop = set(current_ranked[:N_DROP])

            non_held_top = [
                sym for sym, _ in signals_sorted
                if sym not in self._current_holdings
            ][:N_DROP]
            to_add = set(non_held_top)

            target_tickers = (self._current_holdings - to_drop) | to_add

        added = target_tickers - self._current_holdings
        removed = self._current_holdings - target_tickers

        # Liquidate dropped holdings.
        for ticker in removed:
            self.set_holdings(self._symbol_by_ticker[ticker], 0)

        # Open new positions at equal-weight. Surviving positions are NOT
        # touched — they drift with the market until they fall out via the
        # bottom-N_DROP rule.
        weight = 1.0 / TOPK
        for ticker in added:
            self.set_holdings(self._symbol_by_ticker[ticker], weight)

        if added or removed:
            self.log(
                f"rebalance@{today_iso} (signals={signal_date}) "
                f"holdings={len(target_tickers)} +{sorted(added)} -{sorted(removed)}"
            )
        self._current_holdings = target_tickers

    def on_data(self, data: Slice) -> None:
        # All trading logic lives in the scheduled _rebalance handler.
        pass
