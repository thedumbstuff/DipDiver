# M13 · Multi-universe support

> **Goal.** Make the "multi-universe" part of the suggestion-board vision real. SP500 ships first because Alpaca already supports US equities — zero new broker plumbing.

## Universe registry

`dipdiver/brain/baselines/universes.py` exposes `UNIVERSES: dict[str, Universe]` with five entries today:

| Key | Region | Size | Live? | Benchmark |
|---|---|---:|:-:|---|
| `dow30` | us | 30 | ✅ | DJI |
| `sp500` | us | 60+ | ✅ | GSPC |
| `nifty50` | in | 50 | ❌ research-only | NSEI |
| `crypto` | crypto | 3 | ❌ research-only | BTC-USD |
| `world_indices` | world | 14 | ❌ research-only | GSPC |

## `Universe.live_executable`

New flag on the `Universe` dataclass. When `False`, `/picks` shows a research-only banner. The `LiveTradingGate` at M11 enforces this at the broker level too — defence in depth.

## SP500 starter

`_SP500_STARTER` is 60 megacap tickers (Tech, Financials, Healthcare, Consumer/Industrials). For full point-in-time membership, drop a CSV at `data/universes/sp500.csv` (one ticker per line) and the universe automatically extends — `_load_sp500_extension()` walks it.

Configs:
- `dipdiver/brain/baselines/configs/sp500_lightgbm.yaml`
- `dipdiver/brain/baselines/configs/sp500_lstm.yaml`

Both use `topk=30, n_drop=6` to match the wider universe size.

## Universe/config registry API

`dipdiver/ui/routes/registry_api.py` exposes:

- `GET /api/available-configs` — every YAML in `dipdiver/brain/baselines/configs/`. Each entry: `{filename, stem, universe, model_kind}`.
- `GET /api/available-universes` — `UNIVERSES.values()` with `{key, label, size, region, live_executable}`.

Used by `/config` to populate validated dropdowns instead of free-text YAML filenames.

## Tests

- `tests/brain/test_universes_sp500.py` — SP500 in `UNIVERSES`, live_executable flag per universe, YAML parses, `Universe.symbols` alias works.
- `tests/ui/test_picks_route.py::test_api_available_*` — registry endpoints return 200 + sensible payloads.

## Enabling a registered universe (one click)

For universes already in the registry, **/config → Add market** runs the whole
onboarding pipeline as a background job (`dipdiver/ui/jobs/market_onboard.py`):
fetch OHLCV → train + lock the M1 baseline behind the m1_retrain gates →
export signals → append the strategy entries to `ui_config.yaml`. Requires the
`brain` extra (installed in the Docker image; `pip install -e ".[brain]"` for
local dev). Models that fail the gates land as `candidate` on /models and are
NOT enabled.

## Adding a brand-new universe

1. Add `Universe(...)` dataclass to `dipdiver/brain/baselines/universes.py` and the dict.
2. Add YAML configs to `dipdiver/brain/baselines/configs/`.
3. Add the universe to `FETCH_WINDOWS` and `PROVIDER_DIR` in `scripts/m1_setup.py`.
4. If live-tradeable on the current adapter, add the universe key to `dipdiver/adapters/alpaca/gate.SUPPORTED_UNIVERSES`. Otherwise set `live_executable=False`.
5. Then onboard it from /config → Add market as above.
