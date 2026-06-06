# M11 · Live-trading safety gate

> **Goal.** Zero path to real money that does not pass the VALIDATION.md tier ladder, with broker-universe compatibility checked at code level.

## What shipped

- **`dipdiver/adapters/alpaca/gate.py::LiveTradingGate`** — stateless validator. Pass `strategy_id`, call `.check()` → `GateResult(passed: bool, criteria: list[GateCriterion])`.
- **`AlpacaPaperClient(mode='live', strategy_id=...)`** — three-way lockdown:
  1. `mode='live'` must be passed explicitly.
  2. Env `DIPDIVER_LIVE_TRADING=true`.
  3. `LiveTradingGate(strategy_id).check().passed`.
  Any miss → `LiveModeNotAllowedError`.
- **`SUPPORTED_UNIVERSES = {"dow30", "sp500"}`** — Alpaca's US-equity API can't trade `world_indices`/`crypto`/`nifty50`; rejected at the top of `m3_live_alpaca.run_once()`.
- **`LiveGateAudit` table** — every `check()` writes a row with `criteria_json`.
- **`/health` "Live trading gate" card** — per-strategy table showing each criterion vs threshold with green/red pills.
- **`scripts/kill_switch.sh`** — shell companion to the in-app kill switch; cancels orders, flattens positions, writes `DIPDIVER_KILLED` flag.
- **`DIPDIVER_KILLED` flag** — nightly job checks for this file at boot; if present, skips run with a clear message.

## Criteria

```python
{
    "forward_eval_days_min": 60,
    "sharpe_min": 1.0,
    "max_dd_max": 0.10,
    "hit_rate_min": 0.50,
}
```

Override per-call via `LiveTradingGate(strategy_id, thresholds=...)`.

## How to enable live (when you actually have evidence)

1. Accumulate ≥60 days of `(DaySubmittedEvent, PnlSettledEvent)` pairs for the strategy.
2. Verify on `/health` that the live-gate card shows green for all five criteria.
3. Set `DIPDIVER_LIVE_TRADING=true` in the VM env.
4. The code path that wants live mode constructs `AlpacaPaperClient(mode="live", strategy_id="...")`.

The gate is enforced **at construction time** — there is no path that flips `paper=False` on the underlying alpaca-py client without all three conditions met.

## Tests

- `tests/adapters/test_live_gate.py` — every criterion failing in isolation; gate passes when synthetic data clears thresholds; `LiveModeNotAllowedError` paths; `LiveGateAudit` row writes.
