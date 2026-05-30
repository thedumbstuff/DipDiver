# Validation

DipDiver's central thesis: **backtests are not validation.** The open-source agentic-trading landscape is full of repos with impressive backtests and silent live records. This document specifies what we accept as validation and the gates between validation tiers and real capital.

## Tiers of evidence (weakest → strongest)

| Tier | Evidence | What it earns |
|---|---|---|
| 0 | In-sample backtest | Permission to keep iterating. Earns nothing. |
| 1 | Walk-forward backtest with held-out periods | Permission to forward-test on paper. |
| 2 | Forward paper-trade ≥30 days, clean | Permission to scale paper to full universe. |
| 3 | Forward paper-trade ≥60 days, all metrics in spec | Permission to deploy ≤1% of risk capital. |
| 4 | ≥90 days live with metrics holding | Permission to scale within size policy. |
| 5 | ≥12 months live across ≥1 regime shift | Strategy is "validated" — still revocable. |

We are at tier 0 today. Every strategy starts at tier 0.

## The anti-overfit rules

These are non-negotiable. A finding that breaks them is invalid regardless of how good its numbers look.

1. **Time fence.** Any data with timestamp ≥ training cutoff is invisible to the model. The forward-eval harness enforces this at the data layer, not at the model layer.
2. **No retroactive re-training on the test window.** Once a strategy enters tier 2, its parameters are frozen. Re-training restarts the tier-2 clock.
3. **No metric shopping.** Sharpe, max drawdown, hit rate, turnover, and cost-adjusted return are reported together. Cherry-picking one is a defect.
4. **No survivorship.** Universes use point-in-time membership (Nifty 50 as of date D, not Nifty 50 as of today).
5. **Costs in.** Backtests include realistic commission, slippage, and borrow costs from day one. A "before costs" number is not a number.
6. **News & sentiment data are timestamped to their public availability**, not their event time. Lookahead via timestamp sloppiness is the most common silent failure.

## The forward-eval harness

A nightly CI job (Milestone 6 in [`ROADMAP.md`](ROADMAP.md)) runs the full stack — brain → committee → Lean paper broker — on real data, and writes an append-only JSONL scoreboard. Every row contains:

- Date, universe, strategy ID, config hash
- Brain proposal (instruments, weights, confidence)
- Committee transcript (each agent's contribution, final decision)
- Orders placed (Lean events)
- Fills, slippage, commission
- Realised + unrealised P&L
- Position snapshot at close

The scoreboard is **append-only and public**. Edits or deletions are not possible without a force-push that breaks the audit chain.

### Veto-regret tracking

Because the committee can block trades, we need to know whether it's helping. The harness shadows every vetoed trade — "what would have happened if we'd let it through?" — and reports:

- Veto count
- Vetoes that saved money (the rejected trade would have lost)
- Vetoes that cost money (the rejected trade would have won)
- Net veto P&L

If net veto P&L is consistently negative, the committee is demoted to "annotation only" per ADR-003.

### Sanity checks

The harness runs alongside the real strategies:

- A **coin-flip strategy** (50/50 long/short, equal weight): must show no edge over months.
- A **buy-and-hold benchmark** per universe: gives us a reference.
- A **shuffled-features strategy** (RD-Agent's pipeline with feature names randomly permuted): must show no edge.

If any sanity check shows edge, the harness itself is broken — investigate before trusting any other result.

## Capital deployment gates

Before **any** real money flows through the system, all of these must be green. These are layered — passing one doesn't substitute for the others.

| # | Gate | Owner | How verified |
|---|---|---|---|
| 1 | ≥60 days of green forward-eval on the exact strategy/universe | Harness | Scoreboard query |
| 2 | Sharpe > 1.0 on the forward window | Harness | Scoreboard query |
| 3 | Max drawdown < 10% on the forward window | Harness | Scoreboard query |
| 4 | Hit rate > 50% on the forward window | Harness | Scoreboard query |
| 5 | Lean risk-limit module configured (position caps, daily loss limit, blacklist) | Operator | Config diff in PR |
| 6 | Kill-switch tested end-to-end (<60s to flat) | Operator | Recorded test run |
| 7 | First-30-days capital cap ≤ 1% of operator's risk capital | Operator | Self-declared in deploy ticket |
| 8 | Independent review of the broker adapter | Reviewer | Sign-off in PR |
| 9 | Deploy ticket filed, linking to scoreboard rows and PR sign-offs | Operator | Ticket exists |

Gates 1–4 are objective and queryable. Gates 5–9 require human discipline. The repo can't enforce gates 5–9, but it documents them and refuses to ship a "live" preset that bypasses them.

## Kill-switch

A kill-switch is a manual operator action that:

1. Cancels all open orders.
2. Flattens all positions to cash (market orders, accepting slippage).
3. Disables the nightly job so the agent cannot re-enter.

The switch is tested before any live deployment by triggering it on a paper account with live-like positions and confirming all three actions complete in <60s.

## Revocation

Validation is never permanent. Any strategy can be demoted at any time if:

- A metric falls out of spec for >10 consecutive trading days.
- The harness's sanity-check strategies start showing edge (means the harness is broken).
- A regime shift invalidates the assumptions the strategy was trained under.
- The broker adapter fails its conformance suite.

Demotion is automatic at the harness level; re-promotion requires meeting tier gates again.

## What we don't claim

- We don't claim DipDiver makes money. We claim it has a methodology for finding out, honestly.
- We don't claim forward-eval is sufficient — only that it's necessary.
- We don't claim the LLM agents are reasoning. We treat them as pattern-matchers in a gated pipeline, not oracles.

See [`DISCLAIMER.md`](DISCLAIMER.md) for the legal framing.
