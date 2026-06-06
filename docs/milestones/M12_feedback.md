# M12 · Operator feedback loop

> **Goal.** Turn the system from one-way broadcast into a learning loop. Operator thumbs-up/down, override committee, record actual execution divergence; personas learn from veto regret over time.

## Tables (in `dipdiver/ui/db.py`)

- **`UserFeedback`** — `(date, universe, symbol, rating ∈ {-1, 0, 1}, notes, created_utc, actor)`. Used by `/picks` rank penalty.
- **`OverrideDecision`** — `(date, universe, symbol, original_decision, new_decision, reason, …)`. `reason` is required at the API layer (422 otherwise).
- **`ExecutionRecord`** — `(date, universe, symbol, decision, user_action, fraction_taken, notes, …)`. `user_action ∈ {taken, skipped, partial}`.
- **`WatchlistEntry`** — `(symbol, universe, notes, added_utc, actor)`.

## Endpoints (in `dipdiver/ui/routes/decisions.py`)

| Verb | Path | Notes |
|---|---|---|
| POST | `/decisions/{date}/{symbol}/note` | QW11 |
| POST | `/decisions/{date}/{symbol}/feedback` | rating must be -1/0/1 |
| POST | `/decisions/{date}/{symbol}/override` | `reason` required |
| POST | `/decisions/{date}/{symbol}/execution` | `user_action` taken/skipped/partial |
| POST | `/watchlist/add` | (in `routes/picks.py`) |
| POST | `/watchlist/remove` | |
| GET | `/watchlist` | |
| GET | `/persona-accuracy` | scorecards joining `VetoOutcomeEvent` × `OverrideDecision` |

## Rank-penalty math

`apply_feedback_penalty(picks, penalty, lookback_days, universe)` queries `UserFeedback` for `rating=-1` within `lookback_days`. For each match, the pick's score is multiplied by `penalty` (default 0.85 = -15%). Picks are re-sorted afterward; ranks are re-numbered.

## Persona-accuracy scorecards (`/persona-accuracy`)

For each persona:

- `vetoes_with_outcome` — count of vetoes that have a matching `VetoOutcomeEvent` (T+5 has elapsed).
- `veto_regret_avg_pct` — mean counterfactual P&L of vetoed buys. Positive → vetoes cost money.
- `overrides` — count of operator overrides of this persona's vetoes.
- `override_wins` — overrides where the counterfactual was positive (operator was right to override).

Pills: `over-vetoing` (red, regret > 0.5%), `good calls` (green, regret < -0.5%), `neutral` otherwise.

## Tests

- `tests/ui/test_picks_route.py` — note round-trip, feedback round-trip, override requires reason (422), execution round-trip.
- `tests/harness/test_picks.py` — `merge_watchlist` and feedback penalty logic.
- `tests/ui/test_persona_accuracy.py` — zero-state, over-vetoing pill, good-calls pill, override-wins counter.
