# Validation — Forward-Eval, Gates, and Anti-Overfit Rules

## Purpose

DipDiver's central thesis is **backtests are not validation.** The open-source
agentic-trading landscape is full of impressive backtests and silent live
records. This page is the quick reference for what counts as evidence, what
counts as overfit, and what gates a strategy must pass before real capital.

Canonical doc: [`docs/VALIDATION.md`](../docs/VALIDATION.md) — that's the
detailed version; this page is the LLM cheat-sheet.

## Tiers of evidence (weakest → strongest)

| Tier | Evidence                                          | What it earns                                       |
| ---- | ------------------------------------------------- | --------------------------------------------------- |
| 0    | In-sample backtest                                | Permission to keep iterating. Earns nothing.        |
| 1    | Walk-forward backtest with held-out periods       | Permission to forward-test on paper.                |
| 2    | Forward paper-trade ≥30 days, clean               | Permission to scale paper to full universe.         |
| 3    | Forward paper-trade ≥60 days, all metrics in spec | Permission to deploy ≤1% of risk capital.           |
| 4    | ≥90 days live with metrics holding                | Permission to scale within size policy.             |
| 5    | ≥12 months live across ≥1 regime shift            | Strategy is "validated" — still revocable.          |

**Current status:** every strategy is at tier 0. M1 baselines, M2-lite factor
proposer, and M5 committee all produce tier-0 evidence. Tier-1+ requires the
forward-eval harness (M6 — not built yet).

## Anti-overfit rules (non-negotiable)

A finding that breaks any of these is invalid regardless of how good its
numbers look:

1. **Time fence.** Data with timestamp ≥ training cutoff is invisible to the
   model. Enforced at the data layer, not the model layer.
   - In M1: `train_end <= valid_start <= test_start` is enforced in
     `BaselineConfig.__post_init__` (see [m1_baselines.md](m1_baselines.md)).
2. **No retroactive re-training on the test window.** Once a strategy enters
   tier 2, parameters are frozen. Re-training restarts the tier-2 clock.
3. **No metric shopping.** Sharpe, max drawdown, hit rate, turnover, and
   cost-adjusted return are reported together. Cherry-picking one is a defect.
4. **No survivorship.** Universes use point-in-time membership (Nifty 50 as of
   date D, not Nifty 50 as of today).
   - Current gap: `dipdiver/brain/baselines/universes.py` lists *current*
     membership only. Point-in-time membership is enforced at Qlib's data
     layer, not validated upfront. Known limitation.
5. **Costs in.** Backtests include realistic commission, slippage, and borrow
   costs from day one. A "before-costs" number is not a number.
   - M3 Lean algorithm: `DipDiverFeeModel` = 1bp open / 2bp close (see
     [m3_execution.md](m3_execution.md)).
6. **News & sentiment data are timestamped to their public availability**, not
   their event time. Lookahead via timestamp sloppiness is the most common
   silent failure.

## Forward-eval harness (M6 — not built yet)

A nightly CI job that runs the full stack — brain → committee → Lean paper
broker — on real data and writes an append-only JSONL scoreboard. Each row:

- Date, universe, strategy ID, config hash
- Brain proposal (instruments, weights, confidence)
- Committee transcript (each persona's verdict + rationale)
- Orders placed (Lean events)
- Fills, slippage, commission
- Realised + unrealised P&L
- Position snapshot at close

**Scoreboard is append-only and public.** Edits or deletions break the audit
chain (force-push protection).

### Veto-regret tracking

Because the M5 committee can block trades, we need to know whether it's
helping. The harness shadows every vetoed trade — *what would have happened
if we'd let it through?* — and reports:

- Veto count
- Vetoes that saved money (rejected trade would have lost)
- Vetoes that cost money (rejected trade would have won)
- Net veto P&L

**Demotion rule:** if net veto P&L is consistently negative, the committee is
demoted to "annotation only" per ADR-003.

### Sanity checks (must show NO edge)

The harness runs three baselines alongside the real strategies:

- **Coin-flip strategy** (50/50 long/short, equal weight): must show no edge
  over months.
- **Buy-and-hold benchmark** per universe: reference, not a sanity check.
- **Shuffled-features strategy** (LightGBM with feature names randomly
  permuted): must show no edge.

If any sanity check shows edge, **the harness itself is broken** —
investigate before trusting any other result.

## Capital deployment gates

Before any real money flows, all of these must be green. Layered — passing
one doesn't substitute for the others:

| #   | Gate                                                                              | Owner    | Verified by         |
| --- | --------------------------------------------------------------------------------- | -------- | ------------------- |
| 1   | ≥60 days of green forward-eval on the exact strategy/universe                     | Harness  | Scoreboard query    |
| 2   | Sharpe > 1.0 on the forward window                                                | Harness  | Scoreboard query    |
| 3   | Max drawdown < 10% on the forward window                                          | Harness  | Scoreboard query    |
| 4   | Hit rate > 50% on the forward window                                              | Harness  | Scoreboard query    |
| 5   | Lean risk-limit module configured (position caps, daily loss limit, blacklist)    | Operator | Config diff in PR   |
| 6   | Kill-switch tested end-to-end (<60s to flat)                                      | Operator | Recorded test run   |
| 7   | First-30-days capital cap ≤ 1% of operator's risk capital                         | Operator | Deploy ticket       |
| 8   | Independent review of the broker adapter                                          | Reviewer | Sign-off in PR      |
| 9   | Deploy ticket filed, linking to scoreboard rows and PR sign-offs                  | Operator | Ticket exists       |

Gates 1–4 are objective and queryable. Gates 5–9 require human discipline.

## Kill-switch

A manual operator action that:

1. Cancels all open orders.
2. Flattens all positions to cash (market orders, accepting slippage).
3. Disables the nightly job so the agent cannot re-enter.

**Tested before any live deployment** by triggering it on a paper account
with live-like positions and confirming all three actions complete in <60s.

## Revocation

Validation is never permanent. Any strategy can be demoted at any time if:

- A metric falls out of spec for >10 consecutive trading days.
- The harness's sanity-check strategies start showing edge (means the
  harness is broken).
- A regime shift invalidates training assumptions.
- The broker adapter fails its conformance suite.

Demotion is automatic at the harness level; re-promotion requires meeting
tier gates again.

## What we don't claim

- We don't claim DipDiver makes money. We claim it has a methodology for
  finding out, honestly.
- We don't claim forward-eval is sufficient — only that it's necessary.
- We don't claim the LLM agents are reasoning. We treat them as
  pattern-matchers in a gated pipeline, not oracles.

Legal framing: [`docs/DISCLAIMER.md`](../docs/DISCLAIMER.md).

## Where these rules are (or aren't) enforced in code

| Rule                            | Status   | Location                                                                       |
| ------------------------------- | -------- | ------------------------------------------------------------------------------ |
| Time fence (train/valid/test)   | enforced | `dipdiver/brain/baselines/config.py:BaselineConfig.__post_init__`              |
| Costs in backtest               | enforced | `dipdiver/brain/baselines/_qlib/task.py` (open/close cost) and `DipDiverFeeModel` in `lean_projects/dipdiver_dow30_lightgbm/main.py` |
| Point-in-time universe          | partial  | Current-membership only in `universes.py`; Qlib data layer handles historical changes |
| Locked baselines                | enforced | `dipdiver/brain/baselines/results.py:save_locked` refuses overwrite            |
| Verify-on-PR                    | partial  | `m1_run.py --verify` checks lock drift within ±5%, but CI integration is TODO  |
| Forward-eval scoreboard         | not built | M6 milestone                                                                  |
| Veto-regret tracking            | not built | M6 milestone                                                                  |
| Sanity-check strategies         | not built | M6 milestone                                                                  |
| Kill-switch                     | not built | Required before any live deployment                                            |

## Cross-references

- [M1 baselines](m1_baselines.md) — where time-fencing and locked-results live
- [M2-lite](m2_lite.md) — same lock-comparator rule applies to LLM-proposed factors
- [M3 execution](m3_execution.md) — fee models and Lean paper-trading parity
- [M5 committee](m5_committee.md) — vetos that will be tracked by future veto-regret harness
- [Tests](tests.md) — current test coverage of validation invariants
- Canonical doc: [`docs/VALIDATION.md`](../docs/VALIDATION.md)
- Legal framing: [`docs/DISCLAIMER.md`](../docs/DISCLAIMER.md)
