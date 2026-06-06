# M6.3 · Veto-regret backfill

> **Goal.** Measure whether committee vetoes save money or burn alpha. For every vetoed buy, find what the symbol actually did over the holding window and write a `VetoOutcomeEvent`.

## Pipeline

```
nightly_run (T+1 morning UTC)
   └─ veto_backfill.run()
       ├─ scan scoreboard for DaySubmittedEvent rows with committee_active=True
       │   and at least one verdict with approved=False
       ├─ for each (date, universe, strategy_id, symbol) tuple:
       │   ├─ if today < entry_date + holding_window_days: skip (not enough elapsed)
       │   ├─ if already_recorded(..., event_type="veto_outcome", symbol=...): skip
       │   └─ compute_counterfactual(symbol, entry_date, hold_days, provider, today)
       │         → VetoRegret | None
       ├─ for each VetoRegret: append_event(VetoOutcomeEvent(
       │       symbol, settle_date, estimated_entry_price,
       │       actual_price_at_settle, counterfactual_pnl_pct,
       │       holding_window_days))
       └─ per-symbol idempotent
```

## PriceProvider abstraction

Production uses `QlibPriceProvider` — reads `$close` from the Qlib OHLCV store already on disk (no new data fetch). Tests inject `DictPriceProvider(prices={(symbol, date): close})`.

Both providers implement `close_on_or_before(symbol, target_date)` — walks back up to 7 days for holiday tolerance.

Custom provider via `dipdiver.harness.veto_regret.set_provider_factory(callable)`.

## Counterfactual math

```
counterfactual_pnl_pct = (settle_close - entry_close) / entry_close
```

If the vetoed buy would have made money (`counterfactual_pnl_pct > 0`), the committee cost us money. The `/persona-accuracy` page aggregates these per persona.

## Holding window

Default 5 trading days. Configurable per `StrategyConfig.veto_regret_window_days`.

## Per-symbol idempotence

`already_recorded(..., event_type="veto_outcome", symbol=...)` includes a per-symbol check — one DaySubmittedEvent with multiple vetoed buys produces multiple outcome rows on the same date tuple, and re-running the backfill must add zero duplicates.

## When outcomes don't materialise

- `entry_date + hold_days > today` → skipped, will pick up on a future run.
- Entry close missing from Qlib → counted as `no_price_data`, no event written.
- Settle close missing → same.

## Tests

- `tests/harness/test_veto_regret.py` — DictPriceProvider walk-back, positive/negative regret, future settle skipping, per-symbol idempotence, approved buys excluded.
