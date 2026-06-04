# M3 — Lean Backtest + Alpaca Paper Trading (Adapters)

## PURPOSE

M3 is the **execution chassis** that consumes M1/M2 Qlib model predictions and runs them through two independent paths:

1. **Lean backtest** (via Docker) — reproduces M1's strategy in QuantConnect's battle-tested engine, verifying structural backtest ↔ live parity.
2. **Alpaca paper trading** (live, daily-runner) — executes the same strategy against real market data using Alpaca's free paper API.

Both paths share the same signal CSV and the same portfolio rebalancing logic (`compute_target_holdings`), ensuring empirical parity.

## ENTRY POINTS

### For backtest (Lean)
- `lean_projects/dipdiver_dow30_lightgbm/main.py` — the Lean algorithm; reads signals.csv daily, runs TopkDropoutStrategy, logs orders.
- `scripts/m3_export_signals.py` — re-fits M1, extracts test-segment predictions to CSV that Lean consumes.
- `scripts/m3_export_lean_data.py` — converts Qlib's OHLCV store to Lean's ZIP file layout.
- `scripts/m3_verify.py` — day-by-day parity check: Lean's actual orders vs expected TopkDropoutStrategy orders.

### For paper trading (Alpaca)
- `scripts/m3_live_alpaca.py` — one-shot daily runner; loads latest signals, reconciles Alpaca paper positions to target, places orders.
- `dipdiver/adapters/alpaca/client.py` — thin alpaca-py wrapper (paper-only).
- `dipdiver/adapters/alpaca/strategy.py` — shared `compute_target_holdings` logic (mirrors Lean).

## DATA FLOW

### Signal Pipeline: M1 → CSV → Lean/Alpaca

```
M1 Qlib LightGBM fit + predict (dipdiver/brain/baselines/)
         │
         └─ model.predict(dataset) → pandas Series
                    │
                    ▼
         m3_export_signals.py
           converts to SignalSnapshot list
                    │
                    ▼
         dipdiver/adapters/lean/signals.py
           write_signal_csv() → /lean_projects/.../signals.csv
                    │
         ┌──────────┴──────────┐
         ▼                      ▼
    Lean algorithm         m3_live_alpaca.py
    reads daily via       reads latest date
    _load_signals()       via _load_latest_signals()
         │                      │
         └──────────┬───────────┘
                    ▼
         compute_target_holdings()
         (shared TopkDropoutStrategy)
                    │
                    ▼
         TopkDropoutStrategy orders
         (adds, removes per day)
```

### Signal CSV Format

**File:** `lean_projects/dipdiver_dow30_lightgbm/signals.csv` (or `data/signals/dow30_lightgbm.csv`)

**Columns:** `date,symbol,score` (no header in first row; CSV DictReader handles it)

**Example rows:**
```
date,symbol,score
2024-01-02,AAPL,0.025341
2024-01-02,MSFT,0.018943
2024-01-02,NVDA,-0.003012
2024-01-03,AAPL,0.031205
...
```

**Semantics:**
- `date`: ISO date string (YYYY-MM-DD) — the date the signal was computed at (typically close-of-day in Qlib).
- `symbol`: Uppercase ticker in Lean; lowercase in Qlib source (case conversion happens in `_load_signals()` in main.py, line 131).
- `score`: Float, higher = stronger long signal. Directly becomes Insight.magnitude in Lean.

**Generation:** `m3_export_signals.py --m1-config dow30_lightgbm.yaml`
- Loads M1 config (topk, n_drop, test window, costs, universe).
- Fits Qlib model on train + valid, predicts on test segment.
- Converts Qlib pred.pkl (Series indexed by (datetime, instrument)) to CSV via `write_signal_csv()`.
- Skips NaN predictions (typical at window start due to lookback warmup).

## TOPK DROPOUT STRATEGY — SHARED CORE

### Unified Logic: `compute_target_holdings()`

**File:** `dipdiver/adapters/alpaca/strategy.py` lines 13–59

**Signature:**
```python
def compute_target_holdings(
    scored: list[tuple[str, float]],
    current_holdings: set[str],
    topk: int,
    n_drop: int,
) -> tuple[set[str], set[str], set[str]]:
    """Returns (target, adds, removes)."""
```

**Algorithm (matches Qlib's TopkDropoutStrategy exactly):**

1. **Cold start** (no current holdings):
   - `target = top-topk scored instruments`
   - All target instruments are in `adds`.

2. **Rebalance** (has existing holdings):
   - Rank current holdings by today's score (lowest rank first).
   - `to_drop = bottom-n_drop of ranked holdings`.
   - `non_held_top = top-n_drop of instruments NOT currently held`.
   - `target = (current_holdings - to_drop) | to_add`.

3. **Return:** `(target, adds=target - current_holdings, removes=current_holdings - target)`.

**Critical:** Surviving holdings (those in target but already held) are **not touched** — we do NOT rebalance their weights. This avoids daily per-position drift that breaks parity with M1's backtest simulation (where positions carry cost basis and don't rebalance mid-portfolio).

### Lean Implementation

**File:** `lean_projects/dipdiver_dow30_lightgbm/main.py` lines 159–228

**Key points:**
- `_rebalance()` is scheduled to run 1 minute after market open via `schedule.on(..., time_rules.after_market_open(..., 1), ...)` (line 98–102).
- Signal timing: uses `_previous_signal_date()` (line 146–153) to fetch signal dated d-1, driving trades at open of day d. This captures the return from close(d) to close(d+1), matching Qlib's label definition (Ref($close, -2) / Ref($close, -1) - 1).
- Position management: uses `self.set_holdings()` for liquidates (line 214) and equal-weight opens (line 221: `weight = 1.0 / TOPK`).
- Survivors untouched — only `added` positions get set via `set_holdings`, dropped positions are cleared to 0.

**Parity critical:**
- Constants must match `dipdiver/brain/baselines/configs/dow30_lightgbm.yaml`:
  - `TOPK = 10`, `N_DROP = 3`, `OPEN_COST = 0.0001`, `CLOSE_COST = 0.0002`, `INITIAL_CASH = 100_000_000`.
  - These are hardcoded in `main.py` lines 36–40 (Lean Docker container doesn't have Python import of dipdiver package).
- Fee model: custom `DipDiverFeeModel` (lines 51–64) applies percentage-of-notional cost, matching Qlib's model. Lean's default per-share cost would destroy parity.

### Alpaca Implementation

**File:** `scripts/m3_live_alpaca.py` lines 97–208

**Flow in `run_once()`:**
1. Connect to Alpaca paper (line 111).
2. Load latest signal date + scores from signals.csv (line 125).
3. Fetch current holdings (line 129).
4. Call `compute_target_holdings()` (line 134) with topk/n_drop from M1 config.
5. For each symbol in `removes`: call `client.close_position(symbol)` (line 184).
6. For each symbol in `adds`: call `client.open_position(symbol, notional_usd=equity/topk)` (line 194).
7. Log all activity to JSON (line 207).

**Idempotent:** if current holdings already match target, no orders are placed (line 159).

**Market check:** respects market-open unless `--force` is passed (line 120).

## CORE FILES & SIGNATURES

### Signal Adapter: `dipdiver/adapters/lean/signals.py`

**`SignalSnapshot` dataclass (lines 27–33):**
```python
@dataclass(frozen=True)
class SignalSnapshot:
    date: str       # ISO date "2024-01-02"
    symbol: str     # Uppercase or per-universe convention
    score: float    # Model's predicted return
```

**Key functions:**
- `write_signal_csv(rows: Iterable[SignalSnapshot], target: Path) -> int` (lines 36–47) — writes signal rows to CSV, returns row count.
- `read_signal_csv(source: Path) -> list[SignalSnapshot]` (lines 50–59) — reads CSV back for tests/verify scripts.
- `signals_from_qlib_pred(pred_path: Path) -> list[SignalSnapshot]` (lines 62–85) — converts Qlib's pickled pred (pandas Series indexed by (datetime, instrument)) to SignalSnapshots. Skips NaN rows.

### Alpaca Client: `dipdiver/adapters/alpaca/client.py`

**`AlpacaPaperClient` class (lines 34–123):**

```python
class AlpacaPaperClient:
    """Minimal Alpaca paper-trading client."""
    
    def __init__(self, api_key: str | None = None, 
                 api_secret: str | None = None) -> None:
        # Reads ALPACA_API_KEY / ALPACA_API_SECRET from env or args.
        # Raises RuntimeError if missing.
        # Lazy-imports alpaca.trading.TradingClient and StockHistoricalDataClient.
        # Always paper=True; no live-money mode.
```

**Critical:** `paper=True` is hardcoded (line 54). No production mode exists in this class.

**Key methods:**
- `get_account() -> AccountSnapshot` — cash, equity, buying_power, status.
- `market_is_open() -> bool` — checks clock.is_open.
- `get_positions() -> list[PositionSnapshot]` — filters to qty > 0.
- `open_position(symbol, notional_usd) -> dict` — market BUY via notional (line 93–111).
- `close_position(symbol) -> dict` — market SELL entire position (line 113–122).

**Return types:**
```python
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
```

## PARITY NUMBERS & KNOWN LIMITATIONS

### Backtest Parity: Lean vs M1 (from docs/milestones/M3_execution.md)

**M1 (Qlib) DOW 30 baseline:**
- CAR: 20.53%
- Sharpe (rf=0): 1.29
- Max DD: -17.96%

**Lean backtest (same signals, same topk/n_drop):**
- CAR: 18.85% (2% slippage from different fill model + Lean's default slippage)
- Sharpe: ≈1.48 (less drawdown at tails)
- Max DD: -18.8%

**Assessment:** Acceptable within operational tolerance. Differences attributed to:
- Lean's fill model (daily OHLC bars, fills at open/close, not exactly mid-price).
- Lean's slippage model (separate from our cost model).
- Universe calendar differences (Lean uses QuantConnect's US calendar; Qlib uses standard NYSE).

### Order-Level Parity: `m3_verify.py` Results

**Metric: percentage of trading days where adds/removes match exactly.**

**Current state:**
- ~70.5% of rebalance days show exact match (adds and removes both agree).
- Per-trade (date, symbol, side) overlap: ~91% of expected trades executed (some adds/removes don't rotate as planned).

**Cascade drift phenomenon:**
When one rebalance diverges (e.g., Lean fills a position 1 tick higher, changing its ranking slightly), subsequent rotations use the diverged holdings as the baseline. Over many days, this compounds into a "drift cone" where late-window parity drops to 60–70%.

**Acceptance:** 70.5% is **not** a hard failure. The parity test's true bar is behavioral consistency: the strategy is executing the same *intent* (rotate bottom-3, add top-3) on ~91% of intended trades. The 30% of "mismatched" days are typically minor swaps (e.g., expected {A, B, C} to add, Lean added {A, B, D} due to fill-time ranking shifts). The risk profile and drawdown are similar.

**Known issue in `m3_verify.py`:**
- Lines 146–153: converts Lean's UTC order timestamp to ET date via a naive 5-hour shift (to account for daylight saving). Not bulletproof but sufficient for daily aggregation.
- Doesn't account for partial fills or rejected orders (assumes all orders fill).

## SCRIPTS & WORKFLOWS

### 1. Export Signals: `m3_export_signals.py`

**Usage:**
```bash
python scripts/m3_export_signals.py --m1-config dow30_lightgbm.yaml
# Default output: data/signals/dow30_lightgbm.csv

# Custom output (e.g., directly into Lean project)
python scripts/m3_export_signals.py \
  --m1-config dow30_lightgbm.yaml \
  --output lean_projects/dipdiver_dow30_lightgbm/signals.csv
```

**What it does:**
1. Loads M1 config via `load_config()` (line 121).
2. Initializes Qlib with config's provider URI + region (lines 52–57).
3. Builds M1 task via `build_task(m1)` (line 60).
4. Fits Qlib model on train+valid, predicts on test segment (lines 61–70).
5. Converts pred Series to SignalSnapshots, skipping NaN (lines 72–86).
6. Writes CSV via `write_signal_csv()` (line 96).

**Output:** CSV with row count logged (line 134).

### 2. Export Lean Data: `m3_export_lean_data.py`

**Usage:**
```bash
python scripts/m3_export_lean_data.py --universe dow30
# Default: lean_projects/data/equity/usa/daily/

python scripts/m3_export_lean_data.py \
  --universe dow30 \
  --output-dir lean_projects/my_data/equity/usa/daily
```

**What it does:**
1. Loads universe symbols via `get_universe(universe_name)` (line 100).
2. Initializes Qlib from dipdiver's data store (lines 118–120).
3. For each symbol:
   - Fetches OHLCV from Qlib's D.features (line 59).
   - Formats as Lean's daily-bar CSV (prices scaled x10000, integer format, line 35–45).
   - Writes to a ZIP file under `<output>/equity/usa/daily/<symbol>.zip` (lines 82–86).
4. Returns dict of symbol → row count.

**Format details (line 35–45):**
```
YYYYMMDD 00:00,open*10000,high*10000,low*10000,close*10000,volume
```
E.g., `20240102 00:00,191500,192100,190800,191900,125000000`

### 3. Lean Backtest: `lean_projects/dipdiver_dow30_lightgbm/main.py`

**Entry:** Lean CLI runs `main.py` inside Docker.

**`DipDiverDow30LightGBM` class:**

```python
class DipDiverDow30LightGBM(QCAlgorithm):
    def initialize(self) -> None:
        # 1. Set backtest window (TEST_START/END from constants).
        # 2. Add all DOW30 symbols with custom fee model.
        # 3. Load signals.csv into _signals_by_date dict.
        # 4. Schedule daily rebalance 1 min after market open.
        # 5. Initialize _current_holdings = empty set.
        
    def _load_signals(self) -> dict:
        # Read signals.csv from PROJECT_DIR / "signals.csv".
        # Parse CSV into dict[date] = [(symbol, score), ...].
        # Convert symbol to uppercase.
        # Skip NaN scores and symbols not in DOW30.
        
    def _previous_signal_date(self, today: str) -> str | None:
        # Binary search in sorted signal dates.
        # Return largest date < today.
        
    def _rebalance(self) -> None:
        # 1. Fetch signal from yesterday (via _previous_signal_date).
        # 2. Call TopkDropoutStrategy logic (lines 190–210, inline).
        # 3. Liquidate removed positions via set_holdings(..., 0).
        # 4. Open added positions at equal weight (1.0 / TOPK).
        # 5. Log rebalance event.
        
    def on_data(self, data: Slice) -> None:
        pass  # All logic in scheduled _rebalance.
```

**Run backtest:**
```bash
cd lean_projects/dipdiver_dow30_lightgbm
lean backtest .  # or: lean backtest dipdiver_dow30_lightgbm
```

**Output:** `backtests/<YYYY-MM-DD_HH-MM-SS>/` directory with:
- `report.json` — aggregate metrics (CAR, Sharpe, Max DD, etc.).
- `*-order-events.json` — detailed order log (used by `m3_verify.py`).
- `*-holdings-logs.json` — position snapshots.

### 4. Verify Parity: `m3_verify.py`

**Usage:**
```bash
python scripts/m3_verify.py --m1-config dow30_lightgbm.yaml
# Auto-finds newest Lean backtest under lean_projects/dipdiver_dow30_lightgbm/backtests/

python scripts/m3_verify.py \
  --m1-config dow30_lightgbm.yaml \
  --lean-run lean_projects/dipdiver_dow30_lightgbm/backtests/2026-06-02_21-31-11 \
  --max-mismatch-rows 25
```

**What it does:**
1. Loads M1 config to get topk, n_drop (lines 329–331).
2. Discovers Lean backtest dir (line 345).
3. Finds order-events.json in backtest output (line 349).
4. Simulates expected orders from signals.csv via `simulate_expected_orders()` (line 360):
   - Replays TopkDropoutStrategy in Python for each date.
   - Records expected (adds, removes) per trading day.
5. Parses Lean's actual orders from order-events.json via `parse_lean_orders()` (line 361):
   - Filters to filled orders only.
   - Groups by trading day (converts UTC timestamp to ET, line 154).
   - Builds actual (adds, removes) per day.
6. Diffs expected vs actual via `diff_orders()` (line 365).
7. Prints detailed report (line 366):
   - % of days with full match (adds AND removes agree).
   - % of days with adds match, removes match (separately).
   - Jaccard similarity per side (0.0 = disjoint, 1.0 = identical).
   - First divergence date.
   - List of mismatches (first N rows, configurable).

**Output example:**
```
M3 PARITY: expected (TopkDropoutStrategy on signals.csv) vs Lean actual
==============================================================================
  total trading days compared: 252
  full match (adds + removes):  177 (70.2%)
  adds match:                    191 (75.8%)
  removes match:                 193 (76.6%)
  avg adds Jaccard:              0.954
  avg removes Jaccard:           0.947
  ...
  first mismatch: 2024-01-17
```

### 5. Live Alpaca Runner: `m3_live_alpaca.py`

**Setup (one-time):**
1. Sign up at [app.alpaca.markets](https://app.alpaca.markets).
2. Generate paper API keys from dashboard.
3. Paste into `.env.m2` at repo root:
   ```
   ALPACA_API_KEY=PK...
   ALPACA_API_SECRET=...
   ```
4. `pip install -e ".[m3]"` (adds alpaca-py if not already).

**Usage:**
```bash
# Dry-run: shows planned orders without submitting
python scripts/m3_live_alpaca.py \
  --m1-config dow30_lightgbm.yaml \
  --dry-run --force

# Live paper run (respects market hours)
python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml

# Off-hours (weekend/after-market): bypass guard
python scripts/m3_live_alpaca.py \
  --m1-config dow30_lightgbm.yaml \
  --force
```

**Main flow in `run_once()` (lines 97–208):**

1. **Connect & snapshot** (lines 111–116):
   - Creates `AlpacaPaperClient()`, fetches account snapshot.
   
2. **Check market** (lines 118–122):
   - If market closed and not `--force`, return early (0).
   
3. **Load signals** (line 125):
   - Reads signals.csv, finds **latest date** with `_load_latest_signals()`.
   - Returns (signal_date, [(symbol, score), ...]).
   
4. **Fetch current positions** (lines 129–131):
   - Calls `client.get_positions()`, builds set of symbol strings.
   
5. **Compute target** (lines 134–143):
   - Calls `compute_target_holdings(scored, current_holdings, topk, n_drop)`.
   - Gets (target, adds, removes).
   
6. **Optional M5 committee** (lines 150–156):
   - If `--with-committee`, submits each buy through `dipdiver.brain.m5.review()`.
   - Vetoed symbols removed from `adds`.
   
7. **Place orders** (lines 168–200):
   - Dry-run: logs planned orders without submission.
   - Real: calls `client.close_position(sym)` for removes, then `client.open_position(sym, notional)` for adds.
   - `notional = equity / topk` (equal-weight allocation).
   - Catches exceptions, logs errors.
   
8. **Log run** (line 207):
   - Writes record to `logs/m3_live/<universe>/<YYYY-MM-DD>.json` via `_write_record()`.

**Record structure (`_build_record()`, lines 265–292):**
```python
{
    "timestamp_utc": "2026-06-04T12:30:00",
    "dry_run": False,
    "signal_date_used": "2026-06-03",
    "account": {
        "cash": 95000.0,
        "equity": 150000.0,
        "buying_power": 95000.0,
        "status": "ACTIVE"
    },
    "current_holdings_pre": ["AAPL", "MSFT", ...],
    "target_post": ["AAPL", "MSFT", "NVDA", ...],
    "adds": ["NVDA", "JPM"],
    "removes": ["IBM"],
    "orders": [
        {"id": "abc123", "symbol": "IBM", "side": "sell", "status": "filled", ...},
        {"symbol": "NVDA", "side": "buy", "notional": 15000.0, "status": "pending", ...}
    ],
    "committee_decisions": []
}
```

## GOTCHAS & CALIBRATION NOTES

### Symbol Case Conversion

Qlib stores symbols lowercase (`aapl`). Lean uses `Symbol("AAPL")`. Alpaca uses uppercase tickers.

**Conversion points:**
- `m3_export_signals.py` (line 84): outputs uppercase symbols to CSV.
- `lean_projects/.../main.py` (line 131): converts CSV symbol to uppercase when loading.
- `m3_live_alpaca.py` (line 78): `.upper()` when reading CSV.

**Pitfall:** If universe has symbols with suffixes (e.g., `RELIANCE.NS` in India), the case conversion must be audited. Currently, only tested on uppercase US tickers (DOW 30).

### Signal Timing & The Off-by-One Day

**M1 Qlib label definition:** Ref($close, -2) / Ref($close, -1) - 1
- Computes the 1-day return from close(t+1) to close(t+2).
- A signal generated at end-of-day t thus predicts the return from close(t) to close(t+1).

**Lean execution timing:**
- Signal dated 2024-01-02 (e computed at close of 2024-01-02) drives trades at **open of 2024-01-03**.
- `_rebalance()` runs 1 min after market open and uses `_previous_signal_date()` to fetch signal d-1.

**Critical:** Using signal_date directly (not shifted) would invert strategy (Sharpe becomes negative), as documented in main.py lines 172–177.

### Costs Must Match

**M1 config (line 38 of dow30_lightgbm.yaml):**
```yaml
backtest_params:
  open_cost: 0.0001  # 1 bp
  close_cost: 0.0002 # 2 bps
```

**Lean hardcoded (main.py lines 36–39):**
```python
OPEN_COST = 0.0001
CLOSE_COST = 0.0002
```

**Lean fee model (lines 51–64):**
```python
class DipDiverFeeModel(FeeModel):
    def get_order_fee(self, parameters):
        notional = abs(order.quantity) * price
        rate = OPEN_COST if order.direction == OrderDirection.BUY else CLOSE_COST
        return OrderFee(CashAmount(notional * rate, "USD"))
```

**Why custom fee model?** Lean's default Alpaca brokerage charges per-share (not per-notional); matching Qlib's % model requires overriding.

### Alpaca Paper Is Always Paper

**AlpacaPaperClient.__init__() (line 54):**
```python
self._trading = TradingClient(key, secret, paper=True)
```

**Hardcoded.** There is no live-money constructor. If someone calls `AlpacaPaperClient(api_key, api_secret)` with real credentials, Alpaca's paper API receives them and routes to the paper account anyway (by design at Alpaca's API level). **Not a risk vector** but good to note: this class **can never** submit to live money.

### Cascade Drift in Parity

When a fill happens at a different price/quantity than expected, the ranking on the next rebalance day shifts slightly. Example:
- Expected adds: {A, B, C}; Lean adds {A, B, D}.
- Tomorrow's score ranking changes (D is now held, C isn't).
- Next-day rotation rank is different → diverges further.

Over 250+ trading days, the "drift cone" compounds. This is **not** a code bug; it's a fundamental property of order-dependent strategies. `m3_verify.py` accounts for it via Jaccard similarity, which shows ~95% overlap even when exact sets diverge.

**Acceptance:** per ROADMAP, 70.5% exact match is acceptable; the behavioral parity (rotating the same symbols at similar times) is what matters.

## INTERDEPENDENCIES

- **Upstream:** M1 (Qlib model config + pred.pkl), M2 (signals, though M3 focuses on M1).
- **Downstream:** M6 (forward-eval harness; will add daily signal refresh loop).
- **Related:** ADR-004 (architectural decision for live path separation: Lean for backtest, Alpaca-direct for live).
- **Data:** Qlib data store (US: `data/qlib/us_data`), Lean's Docker container mount of project directory.

## CROSS-REFERENCES

- [M1 Baselines](m1_baselines.md) — Qlib model configuration, train/valid/test split, TopkDropoutStrategy semantics.
- [M5 Committee](m5_committee.md) — Risk veto panel that can block proposed buys in `m3_live_alpaca.py`.
- [M2-lite](m2_lite.md) — Alpha158Plus + LLM factors; produces alternative signals that M3 can consume.
- [Stack decisions](../docs/STACK_DECISIONS.md) — ADRs covering live execution path (Lean backtest vs Alpaca paper).
- [Validation](validation.md) — Capital deployment gates and kill-switch requirements before live trading.
