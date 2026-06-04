# M1 — Qlib Baselines (Brain)

## Purpose

M1 establishes reproducible, locked baseline results for four trading universes (DOW 30, NIFTY 50, crypto basket, world indices) across two model types (LightGBM, LSTM). These locked results are **the acceptance criterion** for every later improvement in the stack. Any claim to have improved the signal must beat the locked baseline on its test window.

The module is deliberately minimal and boring: configs are YAML, no hyperparameter tuning, no bells. This enforces reproducibility and makes the baseline an honest comparator.

## Entry Points

1. **Setup data (one-time)**:
   ```bash
   python scripts/m1_setup.py [--universe dow30] [--force]
   ```
   Fetches OHLCV from Yahoo, dumps to Qlib binary format at `~/.qlib/qlib_data/{us,in,crypto,world}_data/`.

2. **Verify data** (after setup):
   ```bash
   python scripts/m1_verify.py [--universe dow30]
   ```
   Sanity-checks: all instruments present, calendar span OK, no silent NaN columns.

3. **Run baselines**:
   ```bash
   python scripts/m1_run.py [--config nifty50_lightgbm.yaml] [--lock] [--verify]
   ```
   Trains and backtests against locked results. Flags: `--lock` (persist new lock), `--verify` (compare to lock, exit 1 on drift >5%).

4. **Single-config CLI**:
   ```bash
   python -m dipdiver.brain.baselines --config path/to/config.yaml [--lock] [--verify]
   ```

5. **Diagnostic**: NIFTY 50 troubleshooting
   ```bash
   python scripts/m1_diagnose_nifty.py
   ```
   Cross-checks benchmark, universe-wide returns, TMPV history, per-ticker factors vs fresh Yahoo.

## Config Schema (BaselineConfig)

All configs are YAML; loaded and validated in `dipdiver/brain/baselines/config.py:BaselineConfig` (frozen dataclass).

**Required fields:**
- `name`: string (e.g., "dow30_lightgbm") — used in logs and lock naming
- `universe`: string — must be one of: `dow30`, `nifty50`, `crypto`, `world_indices`
- `model`: string — "lightgbm" or "lstm"
- `region`: string — "us", "in", "crypto", or "world" (used for qlib.init)
- `qlib_provider_uri`: string — path to Qlib binary store (e.g., "data/qlib/us_data"); resolved relative to repo root via `dipdiver._paths.resolve_provider_uri()`
- `train_start`, `train_end`, `valid_start`, `valid_end`, `test_start`, `test_end`: YYYY-MM-DD strings
  - **Time-fencing rule**: `train_end <= valid_start` and `valid_end <= test_start` (no overlap; enforced in `config.py:__post_init__`)
- `benchmark`: string — in-store symbol name (e.g., "DJI", "NSEI") used in backtest
- `seed`: int (e.g., 42) — passed to numpy, torch, random for reproducibility

**Optional fields:**
- `model_params`: dict — passed directly to LGBModel/LSTM init (e.g., `loss`, `colsample_bytree`, `learning_rate`)
- `backtest_params`: dict — controls portfolio simulation (e.g., `topk`, `n_drop`, `open_cost`, `close_cost`, `min_cost`)

**Config hash**: Deterministic SHA256 hash of all fields (sorted JSON, first 16 chars) in `config.config_hash` property. Identity of a locked result; any field change invalidates the lock.

**Examples**: `dipdiver/brain/baselines/configs/*.yaml` (8 files: 4 universes × 2 models)

## Universes

Defined in `dipdiver/brain/baselines/universes.py`.

Each is a frozen dataclass with `name`, `region`, `instruments` tuple, `benchmark`, `benchmark_yahoo` (Yahoo ticker for fresh fetches).

- **DOW30**: 30 US blue chips (AAPL, MSFT, JPM, etc.); benchmark DJI
- **NIFTY50**: 50 NSE India stocks (.NS suffix in Yahoo; Qlib strips/translates); benchmark NSEI. **Note**: TMPV.NS (TATAMOTORS legacy) truncated post-Oct 2025 demerger; existing configs use TMPV for continuity.
- **CRYPTO_BASKET**: 3 spot crypto USD-quoted (BTC-USD, ETH-USD, SOL-USD); benchmark = basket itself
- **WORLD_INDICES**: 14 non-US country indices (14 instruments); benchmark S&P 500 (GSPC, also in signals but only for backtest ranking)

All resolved via `get_universe(name: str) -> Universe` (raises ValueError if unknown).

## Data Pipeline

Implemented in `dipdiver/brain/baselines/data.py`. Three-stage: **fetch**, **dump**, **verify**.

### Fetch (`fetch_yahoo`, `fetch_qlib_us_bundle`)

- **Yahoo** (all universes except optional US Qlib bundle): `yfinance.download()` with retry logic (up to 3 attempts, exponential backoff on empty result)
  - Columns renamed: Open→raw_open, Close→raw_close, Adj Close→adj_close
  - Computes factor = adj_close / raw_close; scales OHLCV: `close = adj_close`, `open = raw_open * factor`, `volume /= factor`
  - Returns `close`, `open`, `high`, `low`, `volume`, `factor`, `vwap` (4-price average) as float32
- **Qlib US bundle** (opt-in via `fetch_and_dump(..., prefer_qlib_bundle=True)`): Falls back to Yahoo on failure; bundle is frozen ~2020-11, so unsuitable for recent test windows. Default off.

### Dump (`dump_to_qlib`)

Writes Qlib binary format to `provider_uri/`:

- **Calendar**: `calendars/day.txt` — union of all instruments' trading days, sorted, newline-separated
- **Instrument lists**: `instruments/{all,universe_name}.txt` — tab-separated (symbol, first_date, last_date)
- **Feature bins**: `features/{symbol}/{field}.day.bin` — binary (uint32 start_index + float32 values in little-endian)
  - One .day.bin per (symbol, field) pair. Missing trading days within an instrument's span encoded as NaN.

**Data flow**: Benchmark symbol renamed from Yahoo form (e.g., "^DJI" → "DJI") before dump so Qlib backtest can look it up by in-store name.

### Verify (`verify_store`)

Reads the binary store back; reports:
- Calendar span (first date, last date, day count)
- Instrument coverage (found / expected)
- Missing instruments
- Per-instrument gap counts (NaN days within data span)
- Stale check: calendar end must >= latest `test_end` across configs for the universe (or report `stale=True`)

Return type: `VerifyReport` with `ok` property (True iff all instruments found and not stale).

**Fetch windows** (in `m1_setup.py`): Extended past `test_end` to give Qlib backtest lookahead for rebalances (TopkDropoutStrategy peeks at day-after trade).

## Training & Backtest (Runner & Task Builder)

### Runner (`dipdiver/brain/baselines/runner.py:run_baseline`)

1. Seeds everything (numpy, torch, random) from `config.seed`
2. Calls `_run_qlib_workflow(config) -> dict[str, Any]` with metric results
3. Wraps result into `BaselineResult` dataclass with provenance (git SHA, qlib version, timestamp)

### Task Building (`dipdiver/brain/baselines/_qlib/task.py:build_task`)

Translates `BaselineConfig` to a Qlib workflow dict matching `qlib/examples/benchmarks/{LightGBM,LSTM}/workflow_config_*.yaml`.

- **Label** (constant): `["Ref($close, -2) / Ref($close, -1) - 1"]` — tomorrow's 1-day log return
- **Data handler** (`Alpha158`): 
  - Spans full date range (train_start to test_end), segments sliced per config
  - Infer processors: RobustZScoreNorm (clip outliers), Fillna
  - Learn processors: DropnaLabel, CSRankNorm (on label)
- **Model block**: 
  - LightGBM: `qlib.contrib.model.gbdt.LGBModel` with `config.model_params`
  - LSTM: `qlib.contrib.model.pytorch_lstm.LSTM` with `config.model_params`
- **Portfolio analysis**:
  - Strategy: `TopkDropoutStrategy` — hold top-k ranked names, rotate n_drop per rebalance
  - Backtest: cost model (open_cost, close_cost, min_cost) per config, benchmark comparison

### Metrics Extraction (`dipdiver/brain/baselines/_qlib/metrics.py:extract_metrics`)

Reads `portfolio_analysis/report_normal_1day.pkl` (DataFrame) and derives:

- **Annualised return**: `net.mean() * 252` (252 trading days)
- **Annualised volatility**: `net.std() * sqrt(252)`
- **Sharpe**: annualised_return / annualised_volatility (or 0 if vol = 0)
- **Max drawdown**: min of (equity / equity.cummax() - 1)
- **Hit rate**: fraction of days with positive net return
- **Turnover**: `report["turnover"].mean() * 252` (per-side, annualised)
- **Trade count**: number of days with turnover > 1e-8 (proxy for rebalance events)
- **Benchmark return**: annualised benchmark return (fallback chain: "bench" → "benchmark" if not found)

## Result Schema (BaselineResult)

Frozen dataclass in `dipdiver/brain/baselines/results.py`.

**Fields:**
- `config_hash`, `config_name`, `universe`, `model`: metadata
- `test_start`, `test_end`: test window dates
- `annualised_return`, `annualised_volatility`, `sharpe`, `max_drawdown` (negative), `hit_rate`, `turnover` (per-side), `n_trades`: headline metrics
- `benchmark_annualised_return`, `excess_return` (strategy return - benchmark return): alpha attribution
- `qlib_version`, `git_sha`, `run_timestamp_utc`: provenance

### Locks

Locked results stored at `dipdiver/brain/baselines/locked/{config_hash}.json` (JSON dict of the above).

- **Immutable**: `save_locked()` refuses to overwrite; must delete explicitly to relock
- **Comparison**: `compare(current, locked, tolerance=0.05)` checks headline metrics within ±5% (default). Used by `--verify` to gate reproducibility

## Signal Export

Not directly in M1, but runner generates `SignalRecord` and `SigAnaRecord` via Qlib's workflow machinery. Signal predictions are available in the recorder for downstream consumption (e.g., M3 execution engine).

## Vendored Code (_qlib/)

`dipdiver/brain/baselines/_qlib/` contains private Qlib helpers:
- `task.py`: Config → Qlib workflow dict
- `metrics.py`: Recorder → headline metrics dict
- `__init__.py`: (empty, marks private module)

These are kept separate so the rest of the package stays clean and doesn't import Qlib directly.

## Key Assumptions & Gotchas

1. **Qlib store location**: Configs reference relative paths (e.g., "data/qlib/us_data"), resolved relative to repo root via `resolve_provider_uri()`. Overridable via `DIPDIVER_DATA_ROOT` env var.

2. **Calendar & point-in-time universe**: `universes.py` lists current membership only. Historical point-in-time constraints (e.g., DOW 30 turnover, NIFTY 50 demergers like TATAMOTORS→TMPV) are enforced at backtest time by Qlib's data layer, not validated upfront.

3. **Time fencing**: Train, valid, test windows must not overlap and must be ordered (train_end <= valid_start, valid_end <= test_start). Violating this is a config error.

4. **NIFTY 50 demerger**: TATAMOTORS demerged 2025-10-01 into TMPV (legacy) and new TATAMOTORS. Configs use TMPV for continuity; pre-Oct 2025 price history is in TMPV, not TATAMOTORS. Future locks may need to pivot or drop TMPV if the legacy entity becomes illiquid.

5. **Crypto calendar**: Crypto trades 24/7; M1 uses Yahoo/Qlib calendar (weekday-only). Backtests on this calendar may miss weekend moves. Acceptable for M1 (boring baseline), but noted for M3+ improvements.

6. **Hit rate edge case**: If no data, hit rate = 0 (safe default).

7. **Benchmark**: Must exist in the provider_uri; if missing from backtest, TopkDropoutStrategy silently skips the rank vs benchmark. Check logs for warnings.

## Cross-references

- [Data layout](data_and_lean.md) — upstream raw OHLCV, Qlib bin format, signals CSV
- [M2-lite](m2_lite.md) — LLM-proposed factors that extend M1's Alpha158 feature set
- [M3 execution](m3_execution.md) — backtests against M1 baseline
- [Validation & time-fencing](validation.md) — anti-overfit rules and capital deployment gates
- Qlib docs: [Data format](https://qlib.readthedocs.io/en/latest/component/data.html#qlib-format-data), [Workflow](https://qlib.readthedocs.io/en/latest/component/workflow.html)