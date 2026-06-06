# M14 · Honest metrics, calibration & polish

> **Goal.** Consolidate observability / calibration items that don't fit cleanly into earlier stages but materially improve trust.

## Sortino + Calmar (`metrics.py`)

`dipdiver/brain/baselines/_qlib/metrics.py::extract_metrics` now returns `sortino` and `calmar` alongside the existing `sharpe`. Sortino uses downside-only volatility; Calmar = annualised return ÷ |max drawdown|.

## Benchmark comparison (`harness/benchmark.py`)

`load_series(symbol)` reads `data/benchmarks/<symbol>.csv` (`date,close`). `daily_excess_pct(symbol=, target_date=)` returns the benchmark's daily % change for excess-return reporting. Cache resets via `reset_cache()`.

**Production source not wired**: the daily CSV refresh job (`scripts/m14_fetch_benchmark.py`) is **not yet implemented** — see "Deferred" below. Render falls back gracefully when CSV is missing.

## Per-symbol attribution (`/strategies/<id>`)

`dipdiver/ui/routes/strategies.py::strategy_detail` now computes:

- `exposure_days[symbol]` — count of days the symbol appears in `orders_submitted` (buy side).
- `realised_by_symbol[symbol]` — equal-share allocation of each `PnlSettledEvent.realised_pnl_usd` across that day's `holdings_at_close`.

Top-20 by realised PnL surfaced in the context as `attribution`.

## A/B view (`/strategies-compare?ids=...`)

Side-by-side cards for any number of strategy IDs:
```
GET /strategies-compare?ids=dow30_lightgbm,dow30_lightgbm_committee
```

Nav link added to `base.html` with the dow30 ± committee comparison as the default.

## Persona prompt versioning

`Persona.prompt_version: str = "v1"` (`dipdiver/brain/m5/personas.py`). Bump when you rewrite the system prompt so old verdicts don't pollute new-prompt calibration on `/persona-accuracy`.

## QW10 cost ceiling field

`StrategyConfig.committee_cost_daily_ceiling_usd: float | None = None`. When set, the committee halts further symbol reviews once the daily cost meets the ceiling; remaining buys default to approved with an auto-approval annotation. **Note**: the ceiling is exposed in the data model but not yet enforced in `committee.py` — that's a future code-side hookup once a real ceiling needs to fire.

## Deferred

- `scripts/m14_fetch_benchmark.py` (daily SPY CSV refresh via yfinance) — easy to write, but blocked on a yfinance dependency add.
- `scripts/m1_walkforward.py` (walk-forward backtest report for VALIDATION tier-2 evidence).
- Onboarding tour modal — currently zero-state CTAs only; no Shepherd.js tour.
- Bidirectional Telegram (incoming webhook for `/approve`, `/reject`).
- Sells through committee (`StrategyConfig.review_sells` flag exists, code-side dispatcher not wired).
- Render hook for benchmark excess-return on `/scoreboard`.

## Tests

- `tests/harness/test_benchmark.py` — loader, walk-back, daily-pct, missing-file fallback.
- `tests/ui/test_strategies_compare.py` — empty state, seeded state, attribution context.
