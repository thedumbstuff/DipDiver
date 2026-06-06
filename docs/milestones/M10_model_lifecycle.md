# M10 · Model lifecycle automation

> **Goal.** M1 models retrain on a rolling schedule, expose age + expiry in the UI, never silently predict outside their validation envelope.

## What shipped

- **`BaselineConfig.roll_window(cadence='1y', anchor_date=None)`** (`dipdiver/brain/baselines/config.py`) — returns a copy with all three windows shifted forward, preserving widths. Validates ordering via `__post_init__`.
- **`dipdiver/ui/jobs/m1_retrain.py`** — job that loads each enabled strategy's config, rolls windows, runs the Qlib pipeline, gates the result, records a `ModelVersion` row. Default cron `0 4 1 * *` (monthly, 1st at 04:00 UTC).
- **`ModelVersion` table** (`dipdiver/ui/db.py`) — `(id, config_name, config_hash, locked_on_utc, train_start, train_end, test_start, test_end, sharpe, max_dd, hit_rate, status)`. `status ∈ {candidate, locked, superseded, rejected}`.
- **`dipdiver/ui/routes/models_page.py`** — `GET /models` lists every version with sortable columns; `model_age_badge(test_end)` returns `{tone, label, days_to_expiry}` consumed by both `/models` and the dashboard.

## Gate thresholds

```python
_LOCK_GATES = {
    "sharpe_min": 0.5,
    "max_dd_max": 0.30,
    "hit_rate_min": 0.45,
}
```

A failing retrain stays `candidate`; the previous `locked` row keeps serving signals until a passing retrain supersedes it.

## Age semantics

- `test_end < today` → red badge `"expired Nd ago"`.
- Within 30 days → yellow `"Nd to expiry"`.
- Else → green `"valid Nd"`.

## Signal auto-refresh

When a retrain locks, `m1_retrain` calls `scripts.m3_export_signals.main([...])` to regenerate the signal CSV. Picks reflect the new model on the next `/picks` request.

## Tests

- `tests/brain/test_roll_window.py` — width preservation, ordering, default-anchor=today, cadence validation.
- `tests/ui/test_models_lifecycle.py` — table creation, age badge logic, latest-locked-version selection, `qlib_unavailable` rejection path.
