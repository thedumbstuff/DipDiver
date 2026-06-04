# M6 · Forward-eval harness

> **Goal.** Nightly CI runs the whole stack on paper and writes an append-only public scoreboard. That scoreboard is the only tier-1+ evidence DipDiver accepts (see [`VALIDATION.md`](../VALIDATION.md)).

## What ships in M6.1 (this slice)

- Append-only JSONL event log under `scoreboard/scoreboard.jsonl`.
- Three event types — `day_submitted`, `pnl_settled`, `veto_outcome` — with Pydantic discriminator.
- Backfill of all existing `logs/m3_live/*/*.json` records.
- Per-strategy + per-day Markdown render.
- One-command nightly wrapper that fans m3 → scoreboard.

## What's deferred to M6.2+

- **P&L settlement.** `PnlSettledEvent` schema exists, no writer yet. Needs an Alpaca `portfolio_history` fetch on T+1 (or a `get_position` snapshot at next-day close). This is the highest-value next step — it turns the scoreboard from "rows exist" into "rows tell us if we're making money."
- **Veto-regret tracking.** `VetoOutcomeEvent` schema exists, no writer yet. Needs: for each `committee_verdicts[]` entry where `approved=False`, fetch the symbol's actual close N days later, compute the counterfactual P&L, write the event. Simplest source: Qlib's existing OHLCV store (the price is already on disk).
- **Sanity-check strategies.** `coin-flip` and `shuffled-features` strategies that must show NO edge (per VALIDATION.md). Implemented as new `strategy_id`s with their own daily writers; same scoreboard.
- **Scheduler.** Currently manual. GitHub Actions when we trust the pipeline + commit cadence.
- **SCOREBOARD.md committed to docs/.** Currently rendered to stdout. Wait until CI is the source of truth so the rendered file matches the JSONL it's derived from.

## Event schema (in `dipdiver/harness/scoreboard.py`)

### `DaySubmittedEvent`
Written by `m3_live_alpaca` (via `m6_backfill` or `m6_nightly`) at the end of a trading day's submit cycle.

Carries: target/holdings/adds/removes, the full `committee_verdicts[]` (compact form — per-persona rationales stay in the source m3_live JSON, linked via `source_run_record`), the `orders_submitted[]` (with broker IDs for later fill lookup), account snapshot pre-trade, `market_open_at_submit`, `dry_run` flag.

Identity tuple: `(date, universe, strategy_id, event_type)`. The backfill is idempotent on this tuple.

### `PnlSettledEvent` (writer not built)
Written at T+1 or later, once close prices land. Carries `realised_pnl_usd`, `unrealised_pnl_usd`, `holdings_at_close{symbol→market_value}`, `equity_at_close`. Source field defaults to `"alpaca_portfolio_history"`.

This is a separate event — never a mutation of the original `day_submitted` row — so the audit chain stays intact. The renderer fuses by `(date, universe, strategy_id)`.

### `VetoOutcomeEvent` (writer not built)
Written T+N (typically T+5 or T+10) for each committee veto. Carries `symbol`, `estimated_entry_price`, `actual_price_at_settle`, `counterfactual_pnl_pct`, `holding_window_days`.

If `counterfactual_pnl_pct > 0` across many vetoes, the committee is costing us money — that triggers the ADR-003 demotion to "annotation only."

## Strategy IDs

A `strategy_id` is the unit of A/B comparison on the scoreboard. The current encoding:

```
<universe>_<m1_model>[_committee]
```

Examples:
- `dow30_lightgbm` — DOW 30 baseline, no committee
- `dow30_lightgbm_committee` — same, with M5 risk-veto active
- `nifty50_lightgbm` (future)
- `dow30_lightgbm_coinflip` (future sanity-check strategy)

Each new strategy writes its own daily row. The renderer groups by `strategy_id` for the per-strategy running totals table.

## Run

### Backfill from existing m3_live records

```bash
python scripts/m6_backfill.py --dry-run   # preview
python scripts/m6_backfill.py             # write
```

Idempotent: re-runs skip already-recorded `(date, universe, strategy_id, event_type)` tuples. Dry-run m3 records are skipped by default (they're not evidence).

### Nightly wrapper

```bash
# Off-hours dry-run (no orders, no scoreboard rows)
python scripts/m6_nightly.py --m1-config dow30_lightgbm.yaml --dry-run --force

# Live paper run with committee, scoreboard updated
python scripts/m6_nightly.py --m1-config dow30_lightgbm.yaml --with-committee
```

The wrapper runs `m3_live_alpaca` then `m6_backfill` in sequence. Stops on the first non-zero exit code (an Alpaca failure doesn't corrupt the scoreboard).

### Render

```bash
python scripts/m6_render_scoreboard.py
python scripts/m6_render_scoreboard.py --out docs/SCOREBOARD.md
python scripts/m6_render_scoreboard.py --strategy dow30_lightgbm_committee
```

Prints two tables to stdout (or a file):
1. **Per-strategy totals** — days observed, total orders, buys reviewed/vetoed, aggregate veto rate, committee cost.
2. **Per-day log** — most recent first, with per-day P&L columns once `PnlSettledEvent` writers land.

## Append-only invariant

The scoreboard JSONL is **append-only.** This is the audit chain VALIDATION.md is built on.

- `append_event()` opens in append-binary mode, writes one line, `os.fsync()`. Never re-opens for write.
- Updates are new events, not mutations. P&L lands as a new `pnl_settled` row, not by editing the `day_submitted` row.
- Lines are sorted deterministically only at render time (by date desc, strategy_id asc).
- If a row needs to be superseded (genuine bug, never just "I changed my mind"), the planned `SchemaVersionEvent` will mark the old row as deprecated — still without deleting it.

This means the JSONL grows linearly. At ~1 KB per `day_submitted` event, a year of daily runs across 4 strategies is ~1 MB. Negligible.

## Why a separate harness instead of folding into m3_live

Three reasons:

1. **Append-only file vs per-day JSON.** m3_live writes one JSON per day (overwritable). The scoreboard is one append-only log. Different invariants → different layers.
2. **Multi-source.** The scoreboard will eventually accept events from sanity-check strategies (coin-flip, shuffled-features) that DON'T call Alpaca. Those events come from a different writer entirely.
3. **Backfillability.** The current backfill exists *because* m3_live ran for several days before M6 landed. The harness is designed to ingest from any source that produces structured day records, not just from m3_live.

## Acceptance criteria (from ROADMAP)

- [x] Schema for the full scoreboard event log (`dipdiver/harness/scoreboard.py`).
- [x] Backfill that promotes existing m3_live records (`scripts/m6_backfill.py`).
- [x] Render that produces per-strategy + per-day summary (`scripts/m6_render_scoreboard.py`).
- [x] Nightly entry point (`scripts/m6_nightly.py`).
- [ ] Scoreboard updates every weekday morning automatically (scheduler — M6.2).
- [ ] 30 consecutive days of clean updates with no manual intervention (calendar).
- [ ] A coin-flip strategy added as a sanity check shows up appropriately bad (M6.3).
- [ ] Veto-regret tracked — committee demoted automatically if net negative (M6.2/M6.3).

## Cross-references

- [Validation methodology](../VALIDATION.md) — what counts as evidence; this harness produces it.
- [M3 execution](M3_execution.md) — the source of `day_submitted` events.
- [M5 committee](M5_committee.md) — the source of `committee_verdicts[]` payloads and (eventually) `veto_outcome` shadow tracking.
