# Architecture

DipDiver is a six-layer stack. Each layer is owned by one external project, integrated through narrow, well-defined boundaries. The aim is **maximum reuse, minimum glue**.

## Design principles

1. **Don't reinvent.** Every layer is something a serious team has already built and validated. We adapt, we don't rewrite.
2. **Forward validation is the only validation that counts.** Backtests inform; they do not authorise capital. See [`VALIDATION.md`](VALIDATION.md).
3. **The agent never holds the trigger.** LLM-driven components propose and veto. Deterministic code (Lean) executes. There is no path from an LLM token directly to a broker order.
4. **Parity is sacred.** A strategy that runs in backtest must run identically in paper and live. If the parity check fails, we stop.
5. **Reproduce before trusting.** Any external claim (e.g. RD-Agent(Q)'s ~2× alpha) must be reproduced on our universe before its component is load-bearing.

## Layered view

```
                           ┌────────────────────────────────────────┐
                           │  Forward-Eval Harness                  │
                           │  (live-trade-bench pattern)            │
                           │  Nightly CI · scoreboard · anti-overfit│
                           └────────────────▲───────────────────────┘
                                            │ reads paper-trade results
                                            │
 ┌──────────────┐    signals    ┌───────────┴───────────┐    orders    ┌────────────────┐
 │ Research     │──────────────▶│ Risk-Veto Committee   │─────────────▶│ Execution      │
 │ Brain        │   (factors,   │ (TradingAgents debate)│  (vetoed or  │ Chassis (Lean) │
 │ RD-Agent(Q)  │    weights,   │ qualitative gate ONLY │   approved)  │ Backtest/Paper │
 │ + Qlib       │    targets)   │ cannot create trades  │              │ /Live · parity │
 └──────▲───────┘               └───────────────────────┘              └────────┬───────┘
        │                                                                       │
        │ historical features, returns                                          │ broker calls
        │                                                                       ▼
 ┌──────┴────────────────────────────────────────────┐         ┌──────────────────────────┐
 │ Data Layer (Qlib stores + external feeds)         │         │ Broker Adapters          │
 │ OHLCV · fundamentals · news · sentiment           │         │ Lean native + ported     │
 │                                                   │         │ FinceptTerminal (IN, etc)│
 └───────────────────────────────────────────────────┘         └──────────────────────────┘
```

## Layer detail

### 1. Data layer — Qlib stores + feeds

Qlib's binary store is the canonical historical store (OHLCV, fundamentals, derived features). External feeds (news, sentiment, alt-data) land in side tables keyed to Qlib's calendar so they join cleanly on `(instrument, datetime)`.

Boundary: **everything downstream reads via Qlib's expression engine** (`$close / Ref($close, 1) - 1`, etc.). No raw-file reads from other layers.

### 2. Research brain — RD-Agent(Q) on Qlib

RD-Agent runs a "researcher → developer" loop. The researcher proposes a hypothesis (a new factor, a model variant, a hyperparameter shift); the developer implements and backtests it on Qlib; results feed back to the researcher. Coordinated by o3-class models for ideation, GPT-4.1-class for implementation (per RD-Agent's published config — we'll evaluate substitutes).

Output: a **portfolio decision** — instrument weights or target positions — plus a confidence and a rationale.

Boundary: emits a structured `PortfolioProposal` object. Never calls a broker.

### 3. Risk-veto committee — TradingAgents-style multi-agent debate

A panel of LLM agents (fundamental analyst, news/sentiment, macro, risk manager, optionally persona agents from FinceptTerminal — Buffett, Graham, etc.) reviews each `PortfolioProposal` and the recent regime. They can:

- **Veto** the proposal (full or partial).
- **Annotate** with concerns (logged, not enforced).

They **cannot** create or modify trades. They cannot raise sizes. The committee is a one-way filter, downstream of the brain, upstream of execution.

Rationale: agentic LLMs are good at "this looks wrong" pattern-matching and poor at sizing. Use them for what they're good at; deny them the trigger.

### 4. Execution chassis — QuantConnect Lean

Lean owns the event loop, the order book interaction, fills, slippage modelling, and the backtest↔live parity guarantee. An approved `PortfolioProposal` is translated by a thin adapter (`dipdiver.adapters.lean.PortfolioToInsights`) into Lean `Insight` objects and submitted via the standard portfolio construction → execution pipeline.

Boundary: Lean is the **only** code path that reaches a broker. Period.

### 5. Broker adapters — Lean native + FinceptTerminal-ported

Lean ships brokerage plugins (IBKR, Alpaca, Tradier, OANDA, Binance, etc.). For markets Lean doesn't natively cover — primarily Indian brokers (Zerodha, Angel One, Upstox, Fyers) — we port FinceptTerminal's broker connectors into Lean's `IBrokerage` interface.

Each adapter ships with a conformance test suite (place/cancel/replace/query order, query positions, query balances) before it sees a live key.

### 6. Forward-eval harness — live-trade-bench pattern

A nightly job runs the whole stack against a paper broker on real market data, writes results (positions, P&L, hit-rate, drawdown, agent decisions, vetoes) to a scoreboard. **The scoreboard, not any backtest, is the artefact that gates capital deployment** — see [`VALIDATION.md`](VALIDATION.md) for the exact gates.

The scoreboard is public-by-default in this repo so external observers can audit our forward record.

## Data flow per decision

1. Qlib computes/updates features for the universe.
2. RD-Agent(Q) produces a `PortfolioProposal`.
3. Committee debates; emits an `ApprovalDecision` (approve / veto / partial-veto with sizes capped).
4. Adapter converts approved decision to Lean `Insight`s.
5. Lean's portfolio construction + risk + execution modules produce orders.
6. Broker fills; Lean records.
7. Harness logs the entire trace (proposal, debate transcript, decision, orders, fills, P&L) to the scoreboard.

## Integration boundaries (contracts)

| Boundary | Producer | Consumer | Contract |
|---|---|---|---|
| Features | Qlib | RD-Agent(Q) | Qlib expression API |
| Proposal | RD-Agent(Q) | Committee | `PortfolioProposal` (instruments, weights, horizon, confidence, rationale) |
| Decision | Committee | Lean adapter | `ApprovalDecision` (approved\|vetoed\|capped, reasons) |
| Insights | Lean adapter | Lean | `Insight` objects (Lean native) |
| Fills | Lean | Harness | `OrderEvent` stream (Lean native) |
| Scoreboard rows | Harness | Public | Append-only JSONL |

These contracts are versioned. Breaking a contract requires bumping the version and providing a migration.

## What we deliberately do not do

- **No custom backtester.** Lean has one; Qlib has one. We use both for their respective layers and reconcile in the parity test.
- **No custom LLM framework.** RD-Agent and TradingAgents bring their own.
- **No bespoke data store.** Qlib's binary format is good enough; alt-data sits beside it.
- **No "AI orchestrator" abstraction layer.** Each agent system runs in its own process and communicates via the contracts above.
