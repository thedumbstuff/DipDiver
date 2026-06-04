# Data Layout — Qlib Bins, Lean Data Format, Signals CSV

## Overview

DipDiver maintains **three parallel data formats** across the M1 (Qlib) → M3 (Lean) pipeline:

1. **Qlib Binary Store** (`data/qlib/<region>/`) — Source-of-truth OHLCV, factor-adjusted, in Qlib's native `.day.bin` format. Used by M1 (baselines) for backtesting and model training.
2. **Lean ZIP Format** (`lean_projects/data/equity/`) — Daily equity bars in Lean's compact CSV-in-ZIP format, scaled to integers. Read by M3 (Lean strategy engine) during backtesting.
3. **Signals CSV** (`.csv` in Lean projects) — M1 model predictions exported as (date, symbol, score) rows. Read by Lean's Alpha Model to drive portfolio rebalancing.

---

## Layer 1: Qlib Binary Store

### Directory Structure

Under `C:/Shwetank/Work/Workspace/Python/thedumbstuff/DipDiver/data/qlib/`:

```
data/qlib/
  us_data/              # DOW30, NIFTY50 (both use US calendar)
    calendars/
      day.txt           # Newline-separated trading dates (YYYY-MM-DD)
    features/
      aapl/
        open.day.bin    # Float32 binary series, one float per calendar day
        high.day.bin
        low.day.bin
        close.day.bin
        volume.day.bin
        factor.day.bin  # Dividend/split adjustment factor
        vwap.day.bin    # (O+H+L+C)/4
      amgn/
        ...
      (30 DOW symbols, each with 7 fields)
  
  in_data/              # NIFTY50 alternative (not yet actively used)
    calendars/
      day.txt
    features/
      <nifty tickers>/...
  
  crypto_data/          # BTC-USD, ETH-USD, SOL-USD
    calendars/
      day.txt
    features/
      btc-usd/
        open.day.bin
        ...
  
  world_data/           # 14 country indices (^GSPTSE, ^FTSE, etc.)
    calendars/
      day.txt
    features/
      (^GSPTSE)/...
```

### File Format: `.day.bin`

Each `.day.bin` is a **raw binary float32 array** (4 bytes per value, little-endian). No header; values are indexed by position against the calendar:

- **Length** = number of trading days in calendar
- **Value[i]** = field value for calendar[i]
- **Missing data** = NaN (float32 bit pattern `0xFFC00000`)

**Example**: If `day.txt` has 3,000 trading days and `close.day.bin` is 12,000 bytes, then:
- `close.day.bin[0:4]` → first trading day's close (bytes 0-3)
- `close.day.bin[4:8]` → second trading day's close (bytes 4-7)
- etc.

### Qlib Fields (`QLIB_FIELDS`)

All instrument `.day.bin` files share a standard 7-field set:

| Field | Meaning | Notes |
|-------|---------|-------|
| `open` | Opening price (back-adjusted) | Factor-adjusted to today's price scale |
| `high` | Session high (back-adjusted) | |
| `low` | Session low (back-adjusted) | |
| `close` | Closing price (back-adjusted) | **Adjusted = today's adjusted close; historical days scaled down by factor** |
| `volume` | Trading volume (shares) | Scaled inversely by factor so notional is preserved |
| `factor` | Adjustment factor | `adj_close / raw_close`; used to recover raw prices |
| `vwap` | Volume-weighted average price | `(O+H+L+C) / 4` (simplified proxy) |

**Back-adjustment convention** (Qlib/M1 standard):
- Today's bar: raw price = adjusted price
- Historical bars: scaled down by accumulated dividend/split events
- Consumers recover raw price via: `raw = adjusted / factor`

### Calendar Files

`calendars/day.txt` lists all trading days in ISO 8601 format (one per line):

```
2013-01-02
2013-01-03
2013-01-04
2013-01-07
...
2025-12-31
```

No header, no quotes. Dates are **market trading days only** (no weekends/holidays).

---

## Layer 2: Lean ZIP Format

### Location & Naming

Lean expects data under `lean_projects/data/equity/<market>/<frequency>/<symbol>.zip`:

```
lean_projects/data/equity/
  usa/
    daily/
      aapl.zip        # One ZIP per symbol, lowercase
      amgn.zip
      amzn.zip
      ...
  india/
    daily/
      ...
    minute/
      ...
  cfd/oanda/
    daily/
      xauusd.zip
    minute/
      ...
  crypto/coinbase/
    daily/
      btcusd_quote.zip
    minute/
      ...
```

### ZIP Content: CSV Format

Inside each `<symbol>.zip`:

- **Single CSV file** named `<symbol>.csv`
- **Columns**: `date,open,high,low,close,volume` (no header row)
- **Row format**: `YYYYMMDD HH:MM,o,h,l,c,v`
  - Date: `YYYYMMDD` (no dashes), time always `00:00` for daily bars
  - Prices: **integers** (scaled ×10,000), no decimal points
  - Volume: integer (no scaling)

**Example** (first few lines from `aapl.zip`):

```
20140102 00:00,172038,172456,170905,171249,270723744
20140103 00:00,171165,171425,167317,167487,452736352
20140106 00:00,166394,169289,165202,168401,475972608
```

Interpretation:
- 2014-01-02 open: 172038 / 10000 = $17.2038

### Conversion: Qlib → Lean

Script: `scripts/m3_export_lean_data.py`

```python
def export_one_symbol(qlib_symbol, lean_symbol_lower, output_dir, start, end):
    """Read OHLCV from Qlib, write Lean ZIP."""
    from qlib.data import D
    
    # 1. Read Qlib features (already back-adjusted)
    fields = ["$open", "$high", "$low", "$close", "$volume"]
    df = D.features([qlib_symbol], fields, start, end, freq="day")
    
    # 2. Scale prices by 10,000; round to int
    for ts, row in df.iterrows():
        yyyymmdd = ts.strftime("%Y%m%d")
        row_str = f"{yyyymmdd} 00:00,{int(o*10000)},{int(h*10000)},..."
    
    # 3. Write CSV to ZIP
    with zipfile.ZipFile(f"{lean_symbol_lower}.zip", "w") as zf:
        zf.writestr(f"{lean_symbol_lower}.csv", "\n".join(rows) + "\n")
```

**Usage**:

```bash
python scripts/m3_export_lean_data.py --universe dow30 --output-dir lean_projects/data
python scripts/m3_export_lean_data.py --universe nifty50
python scripts/m3_export_lean_data.py --universe crypto
python scripts/m3_export_lean_data.py --universe world_indices
```

---

## Layer 3: Signals CSV

### Purpose

Signals CSV is the **hand-off format** between M1 (Qlib) and M3 (Lean):
- **Created by**: `scripts/m3_export_signals.py` (runs M1 model, exports test-window predictions)
- **Consumed by**: `lean_projects/dipdiver_dow30_lightgbm/main.py` (Lean algorithm reads daily)
- **Role**: Drives M3's Alpha Model → Portfolio Construction → Execution

### Format

**File**: e.g., `lean_projects/dipdiver_dow30_lightgbm/signals.csv`

**Schema** (3 columns, CSV with header):

| Column | Type | Example | Notes |
|--------|------|---------|-------|
| `date` | ISO date string | `2024-01-02` | Trading day |
| `symbol` | Lowercase ticker | `aapl` | Matches Lean universe |
| `score` | Float (6 decimals) | `0.057667` | Qlib model output; higher = stronger buy signal |

**Example** (first 10 rows):

```csv
date,symbol,score
2024-01-02,aapl,0.057667
2024-01-02,amgn,0.055653
2024-01-02,amzn,0.057789
2024-01-02,axp,0.057667
2024-01-02,ba,0.057789
2024-01-02,cat,0.057214
2024-01-02,crm,0.057789
2024-01-02,csco,0.057214
2024-01-02,cvx,0.057789
2024-01-02,dis,0.057667
```

**Statistics** (DOW30 example):
- **Date range**: 2024-01-02 to 2025-12-31 (504 trading days)
- **Symbols per date**: 30 (all DOW30 members, one score each day)
- **Total rows**: 15,060 (504 × 30)
- **File size**: ~375 KB

### Creation: M1 → Signals

Script: `scripts/m3_export_signals.py`

```python
def export_signals(m1_config: BaselineConfig, output_path: Path) -> int:
    """
    1. Load M1 baseline config (e.g., dow30_lightgbm.yaml)
    2. Initialize Qlib with universe's data store
    3. Build & fit M1 model (LightGBM, LSTM, etc.)
    4. Predict on test segment (ignores NaN warmup rows)
    5. Convert Qlib Series(datetime, instrument) → SignalSnapshot CSV
    """
    import qlib
    from dipdiver.brain.baselines._qlib.task import build_task
    
    qlib.init(provider_uri=m1.qlib_provider_uri, region="us")
    task = build_task(m1)
    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])
    
    model.fit(dataset)
    pred = model.predict(dataset)  # Series: (datetime, instrument) → score
    
    # Filter NaN (typical at start of test window due to lookback)
    snapshots = [
        SignalSnapshot(date=dt.strftime("%Y-%m-%d"), symbol=sym, score=float(sc))
        for (dt, sym), sc in pred.items() if sc == sc  # sc != sc ⟺ NaN
    ]
    
    n = write_signal_csv(snapshots, output_path)
    return n
```

**Usage**:

```bash
python scripts/m3_export_signals.py --m1-config dow30_lightgbm.yaml
python scripts/m3_export_signals.py --m1-config dow30_lightgbm.yaml \
    --output lean_projects/dipdiver_dow30_lightgbm/signals.csv
```

### Reading in Lean

File: `lean_projects/dipdiver_dow30_lightgbm/main.py`, lines 88–150

```python
def _load_signals(self) -> dict[str, list]:
    """Pre-load signals CSV into {date_str: [(symbol, score), ...]}.
    
    Called once at initialization; Lean's scheduler calls on_rebalance()
    which looks up yesterday's signals to rebalance today.
    """
    signals_by_date = defaultdict(list)
    csv_path = os.path.join(PROJECT_DIR, SIGNAL_CSV_FILENAME)
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = row["date"]
            symbol = row["symbol"].upper()  # Lean uses uppercase (AAPL)
            score = float(row["score"])
            signals_by_date[date].append((symbol, score))
    
    # Sort each date by descending score (highest rank = most bullish)
    for date in signals_by_date:
        signals_by_date[date].sort(key=lambda x: x[1], reverse=True)
    
    return signals_by_date

def on_rebalance(self) -> None:
    """Daily at market open: rebalance using yesterday's signals.
    
    Strategy: top-k buy signals, n-drop (exit lowest-k from previous hold).
    """
    yesterday = str(self.time.date() - timedelta(days=1))
    if yesterday not in self._signals_by_date:
        self.log(f"No signals for {yesterday}")
        return
    
    signals = self._signals_by_date[yesterday]  # [(symbol, score), ...]
    
    # Top-k rank by score
    targets = {sym: 1.0 / TOPK for sym, _ in signals[:TOPK]}
    
    # Liquidate all others
    for symbol in self.portfolio.keys():
        if symbol not in targets:
            self.liquidate(symbol)
    
    # Size new positions
    for symbol, target_weight in targets.items():
        equity = self.securities[symbol]
        self.set_holdings(equity, target_weight)
```

---

## Universes & Wiring

Four universes are defined in `dipdiver/brain/baselines/universes.py`:

| Universe | Size | Region | Symbols | Qlib Dir | Lean Dir | Status |
|----------|------|--------|---------|----------|----------|--------|
| **DOW30** | 30 | us | AAPL, AMGN, ..., WMT | `data/qlib/us_data/` | `lean_projects/data/equity/usa/daily/` | ✓ Active |
| **NIFTY50** | 50 | in | ADANIENT.NS, ..., ETERNAL.NS | `data/qlib/in_data/` | `lean_projects/data/equity/india/daily/` | ✓ Loaded, not M3-exported |
| **CRYPTO_BASKET** | 3 | crypto | BTC-USD, ETH-USD, SOL-USD | `data/qlib/crypto_data/` | `lean_projects/data/equity/crypto/...` | Partial; path TBD |
| **WORLD_INDICES** | 14 | world | ^GSPTSE, ^FTSE, ^GDAXI, ... | `data/qlib/world_data/` | Not yet set up | Prototype |

### DOW30 (Actively Wired)

- **M1 baseline**: `dipdiver/brain/baselines/configs/dow30_lightgbm.yaml`
- **Qlib source**: `data/qlib/us_data/` (30 symbols + calendar)
- **Lean data**: `lean_projects/data/equity/usa/daily/` (30 `.zip` files)
- **Signals**: `lean_projects/dipdiver_dow30_lightgbm/signals.csv` (15,060 rows)
- **Lean algo**: `lean_projects/dipdiver_dow30_lightgbm/main.py` (top-10, n-drop=3, daily rebalance)

### NIFTY50

- **Qlib source**: `data/qlib/in_data/`
- **Lean wiring**: Defined in universes.py, but **no M3 export yet**
- **Note**: Uses `.NS` suffixes in definition; M1 adapts when loading Qlib

### CRYPTO_BASKET

- **Qlib source**: `data/qlib/crypto_data/` (BTC-USD, ETH-USD, SOL-USD)
- **Lean wiring**: Partial; `lean_projects/data/crypto/` structure exists but export script path is flexible
- **Status**: Test universe for exploring factor models on spot crypto

### WORLD_INDICES

- **Qlib source**: `data/qlib/world_data/` (14 country indices, e.g., ^GSPTSE, ^FTSE)
- **Lean wiring**: Not yet configured
- **Status**: Prototype for global rotation strategies; benchmark is S&P 500 (not in universe)

---

## Data Flow Diagram

```
                        M1 (Qlib)
                            ↓
        [scripts/m1_setup.py] ← Fetch from Yahoo / Qlib bundle
                            ↓
        [scripts/m1_run.py] ← Fit & predict on Qlib data
                            ↓
                        pred.pkl
                            ↓
        [scripts/m3_export_signals.py] ← Convert pred → CSV
                            ↓
                    signals.csv ◄────────────────┐
                            ↓                    │
                        M3 (Lean)                │
        [m3_export_lean_data.py]                 │
                            ↓                    │
        lean_projects/data/equity/.../*.zip      │
                            ↓                    │
        [lean_projects/main.py] ←─ Read CSV ───┘
                            ↓
                   Portfolio rebalance
                            ↓
                    Backtest results
```

---

## Key Gotchas & Notes

### 1. **Back-Adjustment**
- Qlib and Lean both use **back-adjusted prices** (today's adjusted = raw).
- The `factor` field in Qlib lets you recover raw prices if needed.
- M1 and M3 are therefore **consistent**: same entry/exit prices.

### 2. **Symbol Casing**
- **Qlib**: lowercase (`aapl`, `btc-usd`)
- **Lean ZIP**: lowercase filename (`aapl.zip`)
- **Lean CSV**: lowercase in signals.csv (`aapl`) but uppercase in Lean's memory (`AAPL`)
- `main.py` line ~85 converts to uppercase on read

### 3. **NaN Handling**
- Qlib may produce NaN at the start of test window (lookback warmup).
- `m3_export_signals.py` filters these out (line 79: `if score != score`).
- Lean ignores missing dates gracefully (line 92 in main.py).

### 4. **Lean Data Directory**
- Must be relative to Lean's runtime directory (usually `/LeanCLI/`).
- In CI/local testing, symlink `lean_projects/data` → `data/` or set up the project layout.

### 5. **Calendar Dependency**
- Lean infers market hours from the equity's configured exchange (e.g., NASDAQ for US equities).
- The Qlib calendar (`day.txt`) must **match** the market it represents (e.g., `us_data` calendar = US trading days).
- Mismatch causes silent data gaps or invalid bars.

### 6. **Volume Scaling**
- In Qlib, volume is **scaled inversely** by the adjustment factor to preserve notional.
- Lean receives unscaled volume (total shares traded).
- This is correct for backtesting (position sizing works).

---

## File Locations Summary

| Artifact | Path | Type | Owner | Consumer |
|----------|------|------|-------|----------|
| Qlib OHLCV (binary) | `data/qlib/<region>/features/<sym>/*.day.bin` | Float32 array | M1 setup | M1 runner |
| Qlib calendar | `data/qlib/<region>/calendars/day.txt` | Text lines | M1 setup | Qlib.Data |
| Lean daily ZIP | `lean_projects/data/equity/<mkt>/<freq>/<sym>.zip` | CSV in ZIP | m3_export_lean_data | Lean engine |
| Signals CSV | `lean_projects/<project>/signals.csv` | 3-col CSV | m3_export_signals | Lean algo (main.py) |
| M1 baseline config | `dipdiver/brain/baselines/configs/*.yaml` | YAML | Manual | m1_run, m3_export_signals |
| M1 fitted model | `data/models/<config-stem>.pkl` | Pickle | m1_run (trained model) | m3_export_signals (pred only) |

---

## Cross-references

- [M1 Baselines](m1_baselines.md) — Qlib model training pipeline and config structure
- [M3 Execution](m3_execution.md) — Lean strategy engine and backtest harness
- [Validation](validation.md) — Anti-overfit rules including point-in-time universe requirements

> Universe membership is documented inside [m1_baselines.md](m1_baselines.md) — no standalone universes page.
