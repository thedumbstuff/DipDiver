# M9 · Forward-looking suggestion board (`/picks`)

> **Goal.** A non-expert user asks "where do I put money tomorrow?" and gets an answer in one page, mobile-friendly, no drill required.

## What shipped

- **`dipdiver/harness/picks.py`** — data layer with `load_next_signal_forecast()`, `enrich_with_committee()`, `size_by_risk_band()`, `apply_feedback_penalty()`, `merge_watchlist()`.
- **`dipdiver/ui/routes/picks.py`** — `GET /picks?universe=&risk=&strategy_id=` mounts the page.
- **`dipdiver/ui/templates/picks.html`** — mobile-first card grid; one card per pick with conviction pill, weight %, rationale expander, watchlist toggle, "decision detail" link.
- **`dipdiver/ui/templates/dashboard.html`** — "Tomorrow's plan" card now at the top of the dashboard.
- **`UiConfig.risk_band`** (settings.py) — operator-editable.

## Data flow

```
data/signals/<config_stem>.csv  ─► load_next_signal_forecast()  ─► Pick[]
                                                                    │
                              latest DaySubmittedEvent ──► enrich_with_committee() ─► EnrichedPick[]
                                                                    │
                                                       feedback DB ──► apply_feedback_penalty()
                                                                    │
                                                                    ▼
                                                          size_by_risk_band(band)
                                                                    │
                                                       watchlist DB ──► merge_watchlist()
                                                                    │
                                                                    ▼
                                                             picks.html render
```

## Risk-band → weight %

| Band | Per-pick weight | Use case |
|---|---:|---|
| aggressive | 5% | small portfolio, high conviction |
| balanced | 3% | default |
| conservative | 1% | preservation focus |

## Freshness contract

- `signal_freshness_hours(csv_path)` computes hours since the latest signal date.
- `>48h` triggers an amber warning banner on the page.

## Research-only banner

When the requested universe has `Universe.live_executable=False`, a banner appears:
*"🔬 Research-only universe. {universe} cannot be executed live via the current broker adapter."*

## Tests

- `tests/harness/test_picks.py` — data layer (CSV loader, enrichment, sizing, watchlist merge).
- `tests/ui/test_picks_route.py` — `/picks` smoke + all three risk bands + watchlist round-trip + research-only banner.
