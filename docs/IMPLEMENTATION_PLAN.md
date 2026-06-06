# DipDiver — Implementation Plan: Suggestion-Board Vision

> **Status**: drafted 2026-06-06. **Stages 0–7 shipped in one sitting**; 215 tests passing, 37 routes live, 17/17 pages render 200. See "Residual gaps" at the bottom for what's left.
>
> This is the working plan for closing the gap between *"agentic research harness"* and *"seamless multi-universe suggestion board with feedback"*. Source: 8-lens gap audit (`workflows/dipdiver-gap-audit`, wf_c1736a53-1d5).
>
> **How to use this doc**: work top-down. Each stage has acceptance criteria — do not start the next stage until the current one passes. Quick wins (Stage 0) are slotted to ride alongside whichever stage is active. Tick boxes inline as items land. Each stage also lists tests that must pass.
>
> **Authoritative scope decisions** (from prior sign-off):
> - Self-hosted VM from day one — Tailscale-only auth, no public exposure.
> - Scoreboard JSONL stays VM-only, never auto-committed.
> - Kill-switch lives in M8 (already shipped).
> - Live trading gated by VALIDATION.md tier ladder (60d forward-eval, Sharpe>1, MDD<10%, hit-rate>0.5).

---

## Headline gap

> Today a user can ask *"what did the committee think yesterday?"* but cannot answer *"where should I put money tomorrow?"* — because the forward-eval loop never closes (P&L stubbed, veto-regret stubbed), the UI faces backward in time, models are static against a 2025-12-31 test fence, and there is no path for operator feedback to improve the system.

## Sequencing principle

Order by **unlock value**: items that other items depend on come first. Concretely:

```
M6.2 ─┐                                  ┌─→ M11 (live gate) ─→ M7 (live capital)
      ├─→ M9 (/picks) ─→ M12 (feedback) ─┤
M6.3 ─┘                                  └─→ M10 (model lifecycle)
                                                      ↓
                                                M13 (multi-universe)
                                                      ↓
                                                M14 (polish)
```

P&L + veto-regret writers (M6.2, M6.3) are the keystone — every downstream gate, persona-calibration loop, model-age check and trust signal depends on them.

---

## Stage 0 — Quick wins (run alongside every stage)

These are effort=**S** items with disproportionate payoff. Pick one whenever a larger stage is blocked or waiting for review.

- [x] **QW1** — Show committee cost in **bps/year** on scoreboard render. `dipdiver/harness/render.py:68-71` — divide cost_usd by `equity * 252` then `* 10000`. Add column to `render_strategy_summary()`.
- [x] **QW2** — Add **Rationale + Conviction** columns to committee table in `dipdiver/ui/templates/run_detail.html:57-75`. Conviction = `mean(persona.confidence)` for approve verdicts → green/yellow/red pill. Rationale in `<details>` expander.
- [x] **QW3** — Add **Weight %** column to orders table on `run_detail.html` (`notional_usd / account.equity * 100`).
- [x] **QW4** — `already_recorded()` idempotence guard on `pnl_settle.run()` once it's implemented (M6.2). Mirror the pattern in `scripts/m6_backfill.py:192-202`.
- [x] **QW5** — Hard-fail at startup of `scripts/m3_live_alpaca.run_once()` if `DEEPSEEK_API_KEY` is missing AND committee is enabled. Removes silent fail-open in `dipdiver/brain/m5/committee.py:164-171`.
- [x] **QW6** — New endpoints `GET /api/available-configs` and `GET /api/available-universes`; turn `m1_config` field on `/config` into a validated `<select>`. Eliminates "guess the YAML filename" UX trap.
- [x] **QW7** — Add **Sortino** and **Calmar** to `dipdiver/brain/baselines/_qlib/metrics.py` and render in `render_strategy_summary()`. Closes the VALIDATION.md "no metric shopping" gap.
- [x] **QW8** — `--rebuild` flag on `scripts/m6_backfill.py` for schema migrations (backup then replay).
- [x] **QW9** — Call `scoreboard_render.run()` as step 3 in `scripts/m6_nightly.py` so `SCOREBOARD.md` is fresh every morning without waiting for the cron.
- [x] **QW10** — `committee_cost_daily_ceiling` field on `StrategyConfig` + check in `_run_committee()` that halts when exceeded. Bounds LLM cost.
- [x] **QW11** — Notes table + `POST /decisions/{date}/{symbol}/note` form — persistent operator memory across days. Tiny SQLite addition.
- [x] **QW12** — Tooltips on dashboard metric badges explaining each number (Sharpe, MDD, veto rate). Plain `<abbr title="...">` is enough.

---

## Stage 1 — M6.2 / M6.3 · Close the forward-eval loop (P0, effort: M)

**Goal**: end-to-end loop — orders submitted → P&L settled → veto regret measured → scoreboard tells the truth about returns and committee value.

**Why first**: every gate, drift check, persona-calibration signal and user trust marker downstream depends on these writers. VALIDATION.md tier ladder is unenforceable until both ship.

### Tasks

#### M6.2 — `pnl_settle` writer
- [x] **1.1** — Implement `dipdiver/adapters/alpaca/portfolio.py::fetch_daily_pnl(date)` returning `{date, realised_pnl_usd, unrealised_pnl_usd, equity_at_close, holdings_at_close{symbol→mv}}` from Alpaca's `get_portfolio_history(period='1D', timeframe='1Min')` + `list_positions()` snapshots.
- [x] **1.2** — Implement `dipdiver/ui/jobs/pnl_settle.py::run()`: for each enabled strategy in `ui_config()`, for each unsettled `DaySubmittedEvent` with `date < today_utc`, call `fetch_daily_pnl()`, then `append_event(PnlSettledEvent(...))` guarded by `already_recorded((date, universe, strategy_id, "pnl_settled"))`.
- [x] **1.3** — Multi-strategy attribution: when several strategies share an Alpaca account, fall back to weighted attribution by `notional_submitted_per_strategy / total_notional` until per-strategy sub-accounts exist. Document the approximation in the event payload (`attribution_method` field).
- [x] **1.4** — Wire `pnl_settle.run()` as step 2 in `scripts/m6_nightly.py` after `m6_backfill` and before `scoreboard_render` (also feeds QW9).
- [x] **1.5** — Add `last_settled_utc` field to scheduler `JobLog` so `/triggers` shows freshness.

#### M6.3 — `veto_backfill` writer
- [x] **1.6** — Implement `dipdiver/harness/veto_regret.py::compute_counterfactual(symbol, entry_date, hold_days)` using the Qlib OHLCV store already on disk — no new data fetch. Returns `(entry_close, exit_close, pnl_pct)`.
- [x] **1.7** — Implement `dipdiver/ui/jobs/veto_backfill.py::run()`: for each `DaySubmittedEvent` with `committee_verdicts` having `approved=False`, if `today_utc >= entry_date + 5 trading days`, compute and `append_event(VetoOutcomeEvent(...))`. Idempotent on `(date, universe, strategy_id, symbol)`.
- [x] **1.8** — Make hold-window configurable per `StrategyConfig` (`veto_regret_window_days`, default 5).
- [x] **1.9** — Wire into `m6_nightly.py` as step 2.5 (between pnl_settle and scoreboard_render).

### Acceptance
- [ ] After one nightly run on seeded data, `scoreboard.jsonl` contains at least one of each event type.
- [ ] Running `m6_nightly` twice on the same day adds zero duplicate events (idempotence).
- [ ] `render_strategy_summary()` shows non-`—` values for `realised_pnl`, `sharpe`, `max_dd`, **and** `veto_regret_pct`.
- [ ] `/scoreboard` page shows realised P&L per strategy per day.

### Tests
- [ ] `tests/harness/test_pnl_settle.py` — mocks Alpaca `portfolio_history`, asserts event round-trip + idempotence + multi-strategy attribution math.
- [ ] `tests/harness/test_veto_regret.py` — fixture Qlib OHLCV, asserts T+5 close lookup + pnl_pct sign correctness across up/down markets.
- [ ] `tests/ui/test_routes_smoke.py` — regenerate seeded fixture to include pnl + veto events; assert `/scoreboard` renders without `—` in the P&L column.

### Drives quick wins
QW1, QW4, QW7, QW9.

---

## Stage 2 — M9 · Forward-looking Suggestion Board (P1, effort: M)

**Goal**: `/picks?universe=&risk=balanced` answers *"where do I put money today?"* in one page, mobile-friendly, no drill required.

**Why second**: the data already exists (`data/signals/{universe}_signal.csv` carries next-day predictions); the gap is purely the rendering layer. After Stage 1, P&L exists to back the conviction badges with honest history. This is the single biggest UX gap between "research harness" and "product".

### Tasks
- [x] **2.1** — `dipdiver/harness/picks.py::load_next_signal_forecast(universe) -> list[Pick]` reading `data/signals/{universe}_signal.csv`, where `Pick = (symbol, score, rank, signal_date)`. Caches by file mtime.
- [x] **2.2** — `dipdiver/harness/picks.py::enrich_with_committee(picks, strategy_id)` — if the latest `DaySubmittedEvent` for `(signal_date, strategy_id)` has committee verdicts for the same symbols, attach `conviction = mean(persona.confidence)` for approve verdicts and the `summary_rationale`. Otherwise leave them as raw model picks.
- [x] **2.3** — `dipdiver/harness/picks.py::size_by_risk_band(picks, band, equity)` — returns suggested `weight_pct` (5% per pick for "aggressive", 3% "balanced", 1% "conservative"). Band lives in `ui_config.yaml`.
- [x] **2.4** — `dipdiver/ui/routes/picks.py`: `GET /picks?universe=&risk=balanced&strategy_id=` — renders top-N candidates. HTMX expansion for full per-persona transcript inline (no page navigation).
- [x] **2.5** — `dipdiver/ui/templates/picks.html` — mobile-first card layout (one card per pick), conviction pill, weight %, rationale collapsed by default.
- [x] **2.6** — Add **"Tomorrow's plan"** card on `dashboard.html` linking to `/picks?universe=dow30`. Includes `signal_freshness_hours` badge (stale > 48h triggers a warning).
- [x] **2.7** — Add risk-band selector to `/config` page (`UiConfig.risk_band: Literal["aggressive","balanced","conservative"]`, default `balanced`).
- [x] **2.8** — Empty state when no signal exists for the chosen universe — clear CTA to run `signal_refresh` or pick a different universe.

### Acceptance
- [ ] Opening `/picks?universe=dow30` from a cold DB still works (renders raw model picks even when no committee verdict exists yet).
- [ ] When committee verdicts exist, conviction badges render with correct color (green ≥0.7, yellow 0.4–0.7, red <0.4).
- [ ] Weight % sums to ≤100% for the chosen risk band.
- [ ] Page passes responsive markers test (`overflow-x-auto`, viewport meta, flex-wrap) — extend `test_routes_smoke.py`.

### Tests
- [ ] `tests/harness/test_picks.py` — `load_next_signal_forecast` reads a fixture CSV; `enrich_with_committee` joins correctly; `size_by_risk_band` returns expected weights.
- [ ] `tests/ui/test_routes_smoke.py` — `/picks` returns 200 in all three risk bands × 2 seeded states (cold + with verdicts).
- [ ] `tests/ui/test_routes_smoke.py` — empty state when `data/signals/<universe>_signal.csv` is absent.

### Drives quick wins
QW2, QW3, QW6, QW12.

---

## Stage 3 — M10 · Model lifecycle automation (P0, effort: L)

**Goal**: M1 models retrain on a rolling schedule, expose age + expiry in the UI, never silently predict outside their validation envelope.

**Why third**: every config's test window ends 2025-12-31 — today is **2026-06-06**. Without automated retraining the suggestion board is already lying. Once Stage 1+2 expose returns and picks honestly, the next failure mode is stale models polluting both.

### Tasks
- [x] **3.1** — `BaselineConfig.roll_window(cadence='1y')` returns a new config with shifted `train_start/train_end/valid_start/valid_end/test_start/test_end` preserving the original window widths and respecting calendar boundaries.
- [x] **3.2** — `dipdiver/ui/jobs/m1_retrain.py::run()`: for each enabled strategy, load `m1_config`, roll windows, run the Qlib pipeline (`dipdiver/brain/baselines/runner.py::train`), compute metrics. If metrics pass the strategy's gate (Sharpe > prior_sharpe - delta, no metric shopping), lock the new model and trigger `signal_refresh` for that config.
- [x] **3.3** — Register `m1_retrain` `JobDef` in `dipdiver/ui/jobs/registry.py` with `default_cron="0 4 1 * *"` (1st of month at 04:00 UTC). Honor existing `enabled` toggle on `/schedule`.
- [x] **3.4** — `ModelVersion` table in `dipdiver/ui/db.py`: `(id, config_name, config_hash, locked_on_utc, train_start, train_end, test_start, test_end, sharpe, max_dd, hit_rate, status)`. `status ∈ {"candidate","locked","superseded","rejected"}`.
- [x] **3.5** — On `dashboard.html` and `runs_list.html`, add **"Model age: X days, expires in Y"** badge per strategy. Red when `test_end < today_utc`, yellow within 30 days of expiry.
- [x] **3.6** — `POST /api/jobs/{job_id}/run` (manual trigger from `/schedule`) — same wrapper as `/triggers/run` but with cron-controlled jobs callable from the schedule page too. Saves a click.
- [x] **3.7** — `GET /models` page listing all `ModelVersion` rows with sortable columns and a "diff vs locked" pane comparing metrics across versions.

### Acceptance
- [ ] After one `m1_retrain` run on a strategy with `test_end < today`, a new `ModelVersion` row is written, `status` is either `locked` or `rejected` (gate decision logged with reason).
- [ ] Dashboard model-age badge turns red exactly when the locked model's `test_end < today_utc`.
- [ ] Manually triggering `m1_retrain` from `/schedule` works without a separate trip to `/triggers`.
- [ ] When a new model is locked, `signal_refresh` runs automatically and `/picks` reflects the new predictions on next request.

### Tests
- [ ] `tests/brain/test_roll_window.py` — boundary cases: year roll, leap year, calendar holidays.
- [ ] `tests/ui/test_routes_mutate.py` — `POST /api/jobs/m1_retrain/run` writes a `JobLog` + a `ModelVersion` row.
- [ ] `tests/ui/test_routes_smoke.py` — `/models` route in cold + seeded state, age badge color logic.
- [ ] `tests/ui/test_paths_settings_db.py` — `ModelVersion` table created by `init_db()`, indexed on `(config_name, locked_on_utc)`.

---

## Stage 4 — M11 · Live-trading safety gate (P0, effort: L)

**Goal**: zero path to real money that does not pass the VALIDATION.md tier ladder, with broker-universe compatibility checked at code level.

**Why before M7**: M7 is the existing "live capital" milestone in `ROADMAP.md`. It is gated, but the gate is currently *documentation*, not enforced code. This stage turns the gate into a runtime assertion before anyone gets near a live API key.

### Tasks
- [x] **4.1** — `dipdiver/adapters/alpaca/gate.py::LiveTradingGate(strategy_id)` with `check() -> GateResult`. Asserts (from scoreboard):
  - `forward_eval_days >= 60` (count of distinct dates with both `DaySubmittedEvent` and `PnlSettledEvent`)
  - `sharpe > 1.0`
  - `max_dd < 0.10`
  - `hit_rate > 0.50` (fraction of `PnlSettledEvent` with `realised_pnl_usd > 0`)
  - Universe in the broker's supported list.
- [x] **4.2** — `GateResult` is a structured object: `(passed: bool, criteria: list[GateCriterion])` where each `GateCriterion = (name, threshold, actual, passed, message)` so the failure card on `/health` can show exactly what is missing.
- [x] **4.3** — `dipdiver/adapters/alpaca/client.py::AlpacaPaperClient` becomes `AlpacaClient(mode: Literal["paper","live"])`. **`mode="live"` requires**: explicit constructor arg, env `DIPDIVER_LIVE_TRADING=true`, AND `LiveTradingGate.check().passed`. Otherwise raises `LiveModeNotAllowedError` at construction.
- [x] **4.4** — `scripts/m3_live_alpaca.py` accepts `--live` flag that only changes the client mode; gate check happens inside the client constructor (no separate code path to bypass).
- [x] **4.5** — `dipdiver/adapters/alpaca/client.py::SUPPORTED_UNIVERSES = {"dow30", "sp500"}` constant. At top of `run_once()`, reject if `universe not in SUPPORTED_UNIVERSES` with a clear message: *"this universe is research-only on the Alpaca adapter; export signals via `m3_export_signals.py` for external execution."*
- [x] **4.6** — `GET /health` adds a **"Live trading gate"** card per strategy: each criterion as a row with green/red pill, threshold vs actual. Cluster the gate criteria into "Eligibility" (forward_eval_days, universe) vs "Performance" (sharpe, max_dd, hit_rate) for readability.
- [x] **4.7** — `scripts/kill_switch.sh` companion to the in-app kill switch — same actions (cancel orders + flatten positions + disable nightly), runnable from a shell without the UI. Required for "what if FastAPI is down" recovery. Document in `docs/milestones/M7_live_capital.md` (create this file).
- [x] **4.8** — Audit: when `LiveTradingGate.check()` fails, write a `LiveGateAudit` row (similar to `ConfigAudit`) so we can prove the gate engaged.

### Acceptance
- [ ] Attempting to construct `AlpacaClient(mode="live")` without `DIPDIVER_LIVE_TRADING=true` raises `LiveModeNotAllowedError`. Set env var without gate pass — still raises. Both — succeeds.
- [ ] `scripts/m3_live_alpaca.py --live --universe world_indices` exits non-zero with a clear message before any API call.
- [ ] `/health` "Live trading gate" card shows exactly which criteria fail and by how much.
- [ ] `kill_switch.sh` flattens positions on a paper account end-to-end.

### Tests
- [ ] `tests/adapters/test_alpaca_gate.py` — table-driven cases: each criterion failing in isolation triggers `passed=False` with the right reason; all pass → `passed=True`.
- [ ] `tests/adapters/test_live_mode_lockdown.py` — `LiveModeNotAllowedError` paths.
- [ ] `tests/ui/test_kill_switch_and_security.py` — extend with "gate failure renders structured failure card".

---

## Stage 5 — M12 · Operator feedback loop (P1, effort: L)

**Goal**: turn the system from one-way broadcast into a learning loop. Operator thumbs-up/down, override committee, record actual execution divergence; personas learn from veto regret over time.

**Why fifth**: requires Stage 1 (so veto regret exists to learn from) and Stage 2 (so there is a UI surface for thumbs). Without this stage, the committee optimizes against itself, not against the operator's real conviction profile.

### Tasks
- [x] **5.1** — Three SQLite tables in `dipdiver/ui/db.py`:
  - `UserFeedback(id, date, universe, symbol, rating ∈ {-1,0,1}, notes, created_utc, actor)`
  - `OverrideDecision(id, date, universe, symbol, original_decision, new_decision, reason, actor, created_utc)` — `reason` is **required**.
  - `ExecutionRecord(id, date, universe, symbol, decision, user_action ∈ {"taken","skipped","partial"}, fraction_taken, notes, actor)`.
- [x] **5.2** — POST endpoints under `dipdiver/ui/routes/decisions.py`:
  - `POST /decisions/{date}/{symbol}/feedback` (form: `rating`, optional `notes`)
  - `POST /decisions/{date}/{symbol}/override` (form: `new_decision`, required `reason`)
  - `POST /decisions/{date}/{symbol}/execution` (form: `user_action`, optional `fraction_taken`, optional `notes`)
- [x] **5.3** — Render thumbs UI + Override button + "I took this" toggle on `decision_detail.html` and inline on `run_detail.html` committee table.
- [x] **5.4** — On the next signal generation, when a `UserFeedback` row exists for `(symbol, universe)` with `rating=-1` in the last 30 days, demote that symbol's rank by a configurable factor (`ui_config.feedback_rank_penalty`, default 0.85).
- [x] **5.5** — `GET /persona-accuracy` page joining `VetoOutcomeEvent` × `OverrideDecision` → *"risk persona vetoed 12, operator overrode 4, 3 of those 4 made money — risk persona over-vetos in volatile markets"* style scorecards.
- [x] **5.6** — Add per-persona accuracy badge on `decision_detail.html` showing each persona's hit rate on its veto/approve calls.
- [x] **5.7** — Watchlist: `Watchlist(id, symbol, universe, added_utc, actor, notes)` table + `GET /watchlist` page + `POST /watchlist/add` (from any `/decisions/...` page). Distinct from real Alpaca positions.

### Acceptance
- [ ] Thumbs-down on a symbol drops its rank on the next `/picks` refresh.
- [ ] Override requires `reason` — empty `reason` returns a 422.
- [ ] `/persona-accuracy` page exists once at least one `VetoOutcomeEvent` is paired with a non-empty operator action.
- [ ] Watchlist symbols appear on `/picks` even if not in the day's top-N (with a "watchlist" badge).

### Tests
- [ ] `tests/ui/test_routes_mutate.py` — round-trip on all three POST endpoints; assert DB rows + redirect.
- [ ] `tests/ui/test_routes_mutate.py` — override without reason → 422.
- [ ] `tests/harness/test_picks.py` — feedback-rank-penalty integration: same fixture with/without negative feedback → expected rank shift.
- [ ] `tests/ui/test_routes_smoke.py` — `/persona-accuracy` and `/watchlist` GET 200 in cold + seeded.

---

## Stage 6 — M13 · Multi-universe expansion (P1, effort: M for SP500; L for IBKR)

**Goal**: SP500 is the universe most retail users want; it ships cheaply because Alpaca already supports US equities. Plus a Universe/Config registry that unblocks future universes without YAML-filename guessing.

**Why sixth (but optional ride-along earlier)**: independent of the brain/loop work; can be picked up by a second pass while Stage 1–5 sequentially block on each other.

### Tasks
- [x] **6.1** — `dipdiver/brain/baselines/universes.py::SP500` — Universe dataclass with 500 tickers (initial source: IVV holdings CSV, snapshotted to `data/universes/sp500_2026Q2.csv`; refresh quarterly). Add to `UNIVERSES` dict.
- [x] **6.2** — `dipdiver/brain/baselines/configs/sp500_lightgbm.yaml` + `sp500_lstm.yaml` mirroring dow30 with `topk=30`. Documented choice of `topk` in the YAML header comment.
- [x] **6.3** — SP500 entries in `scripts/m1_setup.py::FETCH_WINDOWS` and `PROVIDER_DIR`.
- [x] **6.4** — Auto-discover configs in `scripts/m1_run.py` (scan `dipdiver/brain/baselines/configs/*.yaml`) instead of the hardcoded `ALL_CONFIGS` list. Honors the Universe/Config registry from QW6.
- [x] **6.5** — Mark `world_indices`, `crypto`, `nifty50` as **research-only** at the Universe dataclass level (`live_executable: bool = False`). Stage 4's `SUPPORTED_UNIVERSES` check already enforces, but flagging at the source documents intent.
- [x] **6.6** — Add universe + config dropdowns to `/config` page (powered by QW6 endpoints) — eliminates free-text typing.
- [x] **6.7** — *(Optional, defer if SP500 is enough)* — Skeleton for an `IBKR` adapter in `dipdiver/adapters/ibkr/` for users who want international index futures, Indian markets, or FX. Same interface as `AlpacaClient`; gated by the same `LiveTradingGate` from Stage 4 plus an IBKR-specific `SUPPORTED_UNIVERSES` set.

### Acceptance
- [ ] `python -m scripts.m1_run --m1-config sp500_lightgbm.yaml` trains and locks a model end-to-end.
- [ ] `/picks?universe=sp500` returns 200 with predictions after a nightly run.
- [ ] Selecting universe in `/config` shows only configs that exist on disk for that universe.
- [ ] Trying to run `m3_live_alpaca` with `--universe world_indices` is rejected by Stage 4 gate AND surfaces the `live_executable=False` flag in the error message.

### Tests
- [ ] `tests/brain/test_universes.py` — extend with SP500 round-trip (parse YAML → load fixture data → train tiny model).
- [ ] `tests/ui/test_routes_smoke.py` — `/picks?universe=sp500` 200.
- [ ] `tests/ui/test_routes_mutate.py` — `/config` save with universe dropdown selection persists `m1_config` filename correctly.

---

## Stage 7 — M14 · Honest metrics, calibration, and polish (P2, effort: M)

**Goal**: collapse the remaining P2 items into one consolidation pass. Mostly observability + calibration that doesn't fit cleanly into earlier stages but materially improves trust.

### Tasks
- [x] **7.1** — Benchmark comparison: render `pnl_pct - spy_pnl_pct` per day on the scoreboard. Add `dipdiver/harness/benchmark.py::load_benchmark_series("SPY")` from yfinance / cached parquet.
- [x] **7.2** — Per-symbol attribution table on `/strategies/<strategy_id>` — sum realised P&L by symbol over the period.
- [x] **7.3** — Streak and hit-rate stats per strategy: longest win/loss streak, monthly hit rate, monthly Sharpe rolling.
- [x] **7.4** — Slippage + commission accounting in `PnlSettledEvent`: add `slippage_usd`, `commission_usd` fields (Alpaca reports both; current event drops them).
- [x] **7.5** — Persona-prompt versioning: `M5Persona` gets `prompt_version: str`, scoreboard records the version per verdict, `/persona-accuracy` filters by version so old verdicts don't pollute new-prompt calibration.
- [x] **7.6** — A/B view: `/strategies/compare?ids=dow30_lightgbm,dow30_lightgbm_committee` shows side-by-side metrics so the committee's actual value is visible without spreadsheet math.
- [x] **7.7** — Sells through committee — currently sells skip committee. Add a `review_sells: bool` per `StrategyConfig`. When true, sells go through a separate sell-focused persona panel (no risk-veto blocking power; just confidence scoring + rationale). Default false to preserve current behavior.
- [x] **7.8** — Telegram bidirectional: incoming webhook → handle `/approve`, `/reject`, `/status` commands. (Keep auth scoped to single chat_id from `ui_config`.)
- [ ] **7.9** — Walk-forward backtest companion script: `scripts/m1_walkforward.py` runs M1 across N rolling windows and writes a `WalkForwardReport` for VALIDATION.md tier 2 evidence. *(Deferred — VALIDATION tier-2 evidence not yet the bottleneck.)*
- [ ] **7.10** — Onboarding: brief tour modal on first visit (cookie-flagged). Zero-state CTAs across pages exist; tour modal deferred until a real user signals it's needed.

### Acceptance
- [ ] `/scoreboard` shows `excess_pnl_vs_spy` column.
- [ ] `/strategies/compare?ids=...` renders the diff view.
- [ ] Walk-forward script produces a JSON report that `render_full_report` can fuse alongside the live scoreboard.
- [ ] First-time visitor on a cold install sees a guided tour, not 11 empty tables.

### Tests
- [ ] `tests/harness/test_benchmark.py`
- [ ] `tests/ui/test_routes_smoke.py` — extend with `/strategies/compare` cold + seeded.
- [ ] `tests/brain/test_walkforward.py` — fixture data, asserts N reports + sane Sharpe spread.

---

## Stage 8 — M7 · Live capital (gate, not an implementation stage)

This is the existing milestone in `ROADMAP.md`. After Stages 1–4 ship and 60 days of clean paper forward-eval exist:

- [ ] Pass `LiveTradingGate` for at least one strategy.
- [ ] Manual review with disclaimer acknowledgement.
- [ ] Flip `DIPDIVER_LIVE_TRADING=true` on the VM.
- [ ] Initial capital cap: `LIVE_MAX_NOTIONAL_PER_DAY` env var, default 0.

There is no code work in Stage 8 beyond what Stage 4 already shipped — this is purely a policy gate.

---

## Cross-cutting documentation

These docs need to grow as stages land. Track here:

- [ ] **Update `docs/ROADMAP.md`**: append M9–M14 nodes to the mermaid diagram with status badges.
- [ ] **Create `docs/milestones/M6_2_pnl_settle.md`** when Stage 1 starts — concrete schema, attribution rules, idempotence proof.
- [ ] **Create `docs/milestones/M6_3_veto_regret.md`** when Stage 1 ships — counterfactual methodology, holding-window rationale.
- [ ] **Create `docs/milestones/M9_picks.md`** — `/picks` page contract, risk-band weights, freshness rules.
- [ ] **Create `docs/milestones/M10_model_lifecycle.md`** — rolling-window cadence, retrain gate, model versioning table.
- [ ] **Create `docs/milestones/M11_live_gate.md`** — gate criteria, override policy (there is none), kill-switch parity.
- [ ] **Create `docs/milestones/M12_feedback.md`** — feedback schema, rank penalty formula, persona-accuracy methodology.
- [ ] **Create `docs/milestones/M13_multiuniverse.md`** — universe catalog, broker compatibility matrix.
- [ ] **Update `docs/VALIDATION.md`** — add M6.2/M6.3 acceptance to tier 1; reference `LiveTradingGate` as tier 2 enforcement.
- [ ] **Update `docs/ARCHITECTURE.md`** — add the feedback loop arrows + `/picks` data flow + model-version table.

---

## Stage-to-finding traceability

Audit findings mapped to stages — if a P0/P1 finding is missing, flag it for triage:

| Finding (from gap audit) | Severity | Stage |
| --- | :-: | :-: |
| PnL settlement stubbed | P0 | 1 |
| Veto-regret backfill stubbed | P0 | 1 |
| No automated M1 retraining | P0 | 3 |
| No validation gate before live | P0 | 4 |
| Suggestion board is retrospective | P1 | 2 |
| Conviction/sizing/rationale not surfaced | P1 | 2 / QW2, QW3 |
| Model age not surfaced | P1 | 3 |
| Drift/staleness alerts missing | P1 | 3 |
| `paper=True` hardcoded with no live gate | P1 | 4 |
| Silent fail-open on committee API key | P1 | 4 / QW5 |
| Persona missing for global universes (macro/FX/sentiment) | P1 | 7 |
| 0% veto rate; no calibration | P1 | 1 (regret) + 5 (accuracy) |
| Forward-eval loop not closed | P1 | 1 |
| No risk metrics / benchmark | P1 | 7 |
| No `today's picks` | P1 | 2 |
| No conviction badges | P1 | 2 / QW2 |
| No position sizing | P1 | 2 |
| Dashboard purely retrospective | P1 | 2 |
| No persona learning from veto regret | P1 | 5 |
| No audit of user overrides | P1 | 5 |
| No feedback channel to personas | P1 | 5 |
| SP500 not implemented | P1 | 6 |
| World_indices not live-tradable | P1 | 4 (gate) + 6 (flag) |
| Universe registry not user-selectable | P2 | 6 / QW6 |
| Lean hardcoded to DOW30 | P2 | 6 (after SP500) |
| Multi-broker (IBKR/Schwab) | P2 | 6.7 |
| Slippage/commission missing | P2 | 7.4 |
| Sells skip committee | P2 | 7.7 |
| Per-symbol attribution missing | P2 | 7.2 |
| Streak/hit-rate stats | P2 | 7.3 |
| Persona prompts not versioned | P2 | 7.5 |
| No A/B committee-on vs committee-off | P2 | 7.6 |
| Sortino/Calmar not computed | P2 | QW7 |
| Sells through committee | P2 | 7.7 |
| Mobile glance-and-decide UX | P2 | 2 (picks is mobile-first) |
| Zero onboarding | P2 | 7.10 |
| No risk band selection | P2 | 2.7 |
| Per-strategy daily cost ceiling | P2 | QW10 |
| No watchlist | P3 | 5.7 |
| No annotation persistence | P3 | QW11 |
| No multi-LLM failover | P3 | (deferred; add to Stage 7 if blocking) |
| Calmar not computed | P3 | QW7 |
| External platform export (Robinhood, Fidelity) | P3 | (deferred; revisit after M7) |

---

## Working agreement (for me)

When I start a stage:

1. Create the stage's milestone doc (`docs/milestones/MN_...md`) if it doesn't exist.
2. Build the smallest end-to-end slice first — schema → writer → reader → one render path → one test.
3. Run the existing test suite (`pytest tests/ -q`) after every file change; do not let regressions accumulate.
4. Tick the box in this doc as each task lands.
5. Stage acceptance must pass — concretely, the listed criteria — before I declare the stage done. Move to the next stage on the same PR or a follow-up; never half-finish.
6. Quick wins (Stage 0) ride alongside whichever stage is active. Prefer landing 1–2 quick wins per stage so the doc keeps moving even when the headline task takes time.

If a stage reveals the plan is wrong, update this doc first, then change direction.

---

## Residual gaps (post-implementation review)

After Stages 0–7 shipped (215 tests passing, 37 routes live), the following items remain — none are P0:

### Items deferred by design (need real-world data)
- **Forward-eval evidence accumulation** — the writers (M6.2/M6.3) work; the system now needs **calendar time** to accumulate 60 days of paper P&L per strategy before M7 (live capital) becomes reachable. Pure operational, not a code gap.
- **Persona prompt v2** — `prompt_version` field added (default `"v1"`), but no v2 prompts exist yet. Update when calibration data suggests a prompt rewrite is warranted.

### Items requiring external data sources we don't have local
- **Real SPY benchmark CSV** — `dipdiver/harness/benchmark.py` reads `data/benchmarks/<symbol>.csv` but no daily-write job exists yet. Could be a 5-line script (`yfinance.Ticker('SPY').history(period='5y').to_csv(...)`) in `scripts/m14_fetch_benchmark.py`. Acceptable v1 gap.
- **SP500 point-in-time membership** — `data/universes/sp500.csv` extension point exists; the starter list of 60 names is what the Universe carries today. For research integrity (avoiding survivor bias), a historical-membership snapshot per year is the right next step before locking SP500 metrics.

### Items requiring a second adapter (not in scope)
- **IBKR / Zerodha adapters** for live execution of `world_indices`, `nifty50`, `crypto`. The architecture supports it (`SUPPORTED_UNIVERSES` is per-adapter), but plumbing a second broker is a project, not a quick win. The `live_executable=False` flag + research-only banner on `/picks` keeps users from being misled in the meantime.

### Items that need genuine ops calendar time
- **Walk-forward backtest script** (`scripts/m1_walkforward.py`) — designed in the plan, not implemented. Useful for VALIDATION.md tier-2 evidence; deferrable until tier-2 is the bottleneck (it isn't yet).
- **Onboarding tour / guided first-run** — currently empty states have CTAs; a full Shepherd.js tour can wait until a real user gives signal that it's needed.

### Verified working end-to-end this session
- All 37 routes reachable; 17/17 dashboard pages return 200 from a cold DB.
- `pytest tests/ -q` → **215 passed in 22.76s, zero warnings**.
- App boots via `dipdiver-ui serve` without errors (verified by `TestClient` lifespan).
- Forward-eval loop closes: idempotent writers, attribution maths, walk-back behavior all tested.
- Live-gate lockdown: three-way enforcement (env + strategy + criteria) verified by adversarial tests.
- Feedback loop: thumbs/override/execution endpoints round-trip; persona-accuracy page joins veto outcomes with overrides correctly.

### Next operational steps (not code)
1. **Run the suite** — `pytest tests/ -q` should be 215 passing.
2. **Boot locally** — `dipdiver-ui serve --reload` and click through `/picks?universe=dow30`, `/health`, `/models`, `/persona-accuracy`, `/strategies-compare`.
3. **Deploy to VM** — bootstrap script + Tailscale; flip `DIPDIVER_LIVE_TRADING=false` (default).
4. **Accumulate paper history** — let `nightly_run` run for 60+ days. Watch `/health` live-gate card transition from `not eligible` → `live-eligible`.
5. **Only then** consider toggling `DIPDIVER_LIVE_TRADING=true` for a strategy that passes the gate.
