# M6.2 · P&L settlement writer

> **Goal.** Every `DaySubmittedEvent` gets a matching `PnlSettledEvent` recording realised + unrealised P&L for that trading day, attributed per strategy.

## Pipeline

```
nightly_run (T+1 morning UTC)
   └─ pnl_settle.run()
       ├─ scan scoreboard for DaySubmittedEvent rows where date < today UTC
       │   that have no matching PnlSettledEvent
       ├─ for each such date:
       │   ├─ fetch_daily_pnl_via_alpaca(client, date) → DailyPnlSnapshot
       │   ├─ attribute_strategies(submitted_events_on_that_date) → StrategyShare[]
       │   └─ for each share: append_event(PnlSettledEvent(
       │         realised = snapshot.realised * weight,
       │         unrealised = snapshot.unrealised * weight,
       │         equity_at_close = snapshot.equity * weight,
       │         attribution_method = "single_strategy" | "weighted_by_notional",
       │         attribution_weight = weight, ...))
       └─ guarded by already_recorded((date, universe, strategy_id, "pnl_settled"))
```

## Attribution rules

`attribute_strategies(submitted_events)`:

1. **Single strategy** on the date → `weight=1.0`, `attribution_method="single_strategy"`.
2. **Multi-strategy** day with non-zero total notional → `weight = notional_i / sum(notional)`, method `"weighted_by_notional"`.
3. **Multi-strategy** day with zero notional (no-trade) → equal split: `weight = 1/N`.

The `attribution_weight` field on `PnlSettledEvent` is persisted so re-derivation is unambiguous.

## Alpaca fetcher

`fetch_daily_pnl_via_alpaca(client, target_date)`:

- Queries `portfolio_history(period='7D', timeframe='1D')`.
- Finds the row matching `target_date`.
- Computes `day_pl` from `profit_loss` (with equity-delta fallback).
- Splits realised vs unrealised via current `get_positions()` snapshot.
- Returns `DailyPnlSnapshot(date, realised_pnl_usd, unrealised_pnl_usd, equity_at_close, holdings_at_close, slippage_usd, commission_usd)`.

## Provider override

`pnl_settle.set_provider_override(fn)` injects a fake provider for tests. Production uses `_make_alpaca_provider()` which constructs `AlpacaPaperClient()` lazily.

## Idempotence

`already_recorded(events, date, universe, strategy_id, event_type="pnl_settled")` returns `True` if a row already exists. Re-running adds zero duplicates.

## Tests

- `tests/harness/test_pnl_settle.py` — attribution math (single, weighted, zero-notional), idempotence, skipping today, provider error handling, equity scaling.
