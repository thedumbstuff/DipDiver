# M1 · Qlib baseline

> **Goal.** A reproducible, boring baseline. Without this we cannot tell whether anything we add later is helping.

## Acceptance criteria (from ROADMAP)

1. Backtest results match Qlib's published numbers within ±5% on the same period.
2. Anyone can reproduce a baseline in under one hour from a clean checkout.
3. Each of six baselines has a locked result file under `dipdiver/brain/baselines/locked/`.

## How M1 is run

Three scripts. Run them in order. **Your only job is to inspect the output of each and confirm the numbers look sane before continuing.**

```
scripts/m1_setup.py    → fetches data, writes Qlib binary stores
scripts/m1_verify.py   → reads the stores back, prints sanity tables — YOU VERIFY
scripts/m1_run.py      → executes all six baselines, prints metrics — YOU VERIFY, then --lock
```

## Prerequisites

> **Python 3.12 venv.** Qlib's wheels do not yet cover Python 3.13.

```bash
py -3.12 -m venv .venv
source .venv/Scripts/activate     # PowerShell: .venv\Scripts\activate.ps1
pip install -e ".[dev,brain]"
```

That installs `pyqlib`, `lightgbm`, `torch`, `yfinance`, and friends.

## Step 1 · Fetch data

```bash
python scripts/m1_setup.py            # all three universes
# or one at a time:
python scripts/m1_setup.py --universe dow30
python scripts/m1_setup.py --universe nifty50
python scripts/m1_setup.py --universe crypto
```

What this does:
- **DOW 30** — tries Qlib's prebuilt US bundle first (richer, includes survivorship-cleaned data). Falls back to Yahoo if pyqlib's downloader fails.
- **NIFTY 50** — pulls from Yahoo (`<ticker>.NS`) and writes a Qlib binary store directly. No external dump script needed.
- **Crypto basket** — pulls BTC/ETH/SOL from Yahoo. Daily bars.

Output paths (defaults — under the repo, gitignored):
- `data/qlib/us_data/`
- `data/qlib/in_data/`
- `data/qlib/crypto_data/`

Override the data root with `DIPDIVER_DATA_ROOT=/path/to/elsewhere` if you want it on a different drive.

Idempotent — re-running skips universes already present unless `--force`.

### What to check
- Script finishes without exceptions.
- Print line `[done] <universe>: N instruments, R rows, M missing day-cells` looks reasonable. **A spike in `M` for one universe means Yahoo had gaps** — investigate before moving on.

## Step 2 · Verify

```bash
python scripts/m1_verify.py
```

This reads each Qlib store back and prints a report per universe:

```
=== dow30 @ ~/.qlib/qlib_data/us_data ===
  instruments: 30/30
  calendar:    2013-01-02 -> 2025-12-31 (3271 days)
  status:      OK
```

### What to verify
- **`instruments: N/N` matches the universe size** (30, 50, 3). Any `MISSING:` line means the fetch dropped a ticker — fix before continuing.
- **Calendar end date is reasonable** (close to today's date, allowing for the fetch window).
- **`status: OK`** at the bottom. The script exits non-zero if any universe fails — use it as a gate.
- If you see `gaps:` lines with high NaN counts on a specific ticker, that ticker has bad data. Common causes: corporate action mid-window, ticker not trading on the full period, Yahoo throttling.

Exit code 0 means all three stores look healthy. Continue.

## Step 3 · Run baselines

```bash
python scripts/m1_run.py              # run all six, print only
```

This iterates over the six configs (`{dow30,nifty50,crypto} × {lightgbm,lstm}`), invokes Qlib's workflow for each, and prints a summary line per run:

```
dow30_lightgbm          sharpe=+0.872  ann_ret=+11.40%  max_dd=-12.30%  hit=53.10%  excess=+3.20%
dow30_lstm              sharpe=+0.612  ann_ret=+ 8.10%  max_dd=-15.40%  hit=51.80%  excess=-0.10%
nifty50_lightgbm        sharpe=+1.014  ann_ret=+18.30%  max_dd=-14.80%  hit=54.40%  excess=+4.10%
...
```

### What to verify
- **All six runs complete without exception** (a `FAILED:` summary at the bottom is the failure flag).
- **LightGBM Sharpe is in the same ballpark as Qlib's published reference numbers** for the same universe and period. Qlib's reference is in `qlib/examples/benchmarks/README.md`. Within ±5% is the acceptance bar.
- **Excess return** (over the benchmark) is positive for LightGBM on at least one universe. If every baseline is negative-excess, something is structurally wrong — probably costs misconfigured or label leakage.
- **Drawdowns are not pathological** (e.g. >50%). If they are, the strategy is leveraged in a way it shouldn't be — check `topk`/`n_drop` against the universe size.

If anything looks off, run one config at a time with `-v` for full Qlib logs:

```bash
python scripts/m1_run.py --config dow30_lightgbm.yaml -v
```

## Step 4 · Lock

When you're satisfied with all six runs:

```bash
python scripts/m1_run.py --lock
```

This writes one JSON file per result into `dipdiver/brain/baselines/locked/<config_hash>.json`. The script refuses to overwrite an existing lock — re-locking requires deleting the old file deliberately.

## Step 5 · Verify reproducibility

Re-run on a clean machine (or just again on the same one to catch non-determinism):

```bash
python scripts/m1_run.py --verify
```

Each row prints `verify=PASS` or `verify=FAIL` based on a ±5% tolerance per metric. The script exits non-zero on any failure. This is the M1 acceptance test — when all six pass on a fresh machine, M1 is done.

## Things that will go wrong (and the answer)

- **Yahoo gaps for NIFTY tickers.** Some tickers list partway through the window (e.g. ZOMATO.NS listed 2021). The dumper records NaN; Alpha158 typically handles it. If a specific ticker shows many gaps, drop it from `NIFTY50` in `dipdiver/brain/baselines/universes.py`.
- **`pyqlib` downloader 403/404 for US bundle.** Falls back to Yahoo automatically. Output looks the same, just slower and slightly less curated.
- **LSTM is non-deterministic across GPUs.** Pin CUDA versions or accept a wider tolerance — add a `--tolerance 0.10` flag if needed (not currently exposed; one-line change to `m1_run.py`).
- **`init_instance_by_config` ImportError on LSTM.** Qlib's class paths have moved between versions. If `qlib.contrib.model.pytorch_lstm` doesn't exist in your pyqlib, look in `qlib.contrib.model.pytorch_lstm_ts` and adjust `_qlib/task.py`.
- **Sharpe drastically different from Qlib's reference.** Most likely cause: our universes (DOW 30 / NIFTY 50 / crypto) are *not* the same as Qlib's reference (CSI 300 / S&P 500). Compare against Qlib's own DOW reference if available; otherwise sanity-check the trade list manually before accepting.

## Definition of done

- All six locked JSONs present in `dipdiver/brain/baselines/locked/`.
- `python scripts/m1_run.py --verify` exits 0 on a fresh machine for all six.
- Reproduction recipe (this doc) is current.
- M2 can start.
