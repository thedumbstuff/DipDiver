# M5 · Risk-veto committee

> **Goal.** A multi-agent LLM debate sits between the brain (M1/M2-lite) and execution (M3). It can block proposed trades but cannot create or enlarge them — a one-way filter, never an amplifier.

## Why a committee at all

Every layer we've built so far is a *single voice*: one model's prediction, one strategy's rotation. The committee adds adversarial review — multiple personas argue from different priors, and only proposals that survive get executed.

This is the "agentic" thing the project is named for. Per ADR-003 in `docs/STACK_DECISIONS.md`:

- The committee runs **downstream of the brain, upstream of execution**.
- It can **veto** a trade, or **annotate** it with concerns (logged but not enforced).
- It **cannot** create a new trade, change direction, or enlarge a size.

The empirical bar: **veto rate stays in 1-30% over many trades**. 0% means it's a noop; 100% means it's broken. We measure this with the `m5_review_signals.py` dry-run.

## Architecture

```
dipdiver/brain/m5/
├── schema.py     — TradeProposal, AgentVerdict, CommitteeDecision (Pydantic)
├── personas.py   — 4 personas (fundamental, technical, risk, value)
├── committee.py  — orchestrator: parallel LLM calls + aggregate
└── __init__.py

scripts/
├── m5_review_signals.py     — STANDALONE dry-run; reviews today's signals without trading
└── m3_live_alpaca.py        — extended with --with-committee flag
```

### Personas

| Name | Lens | What it veto-flags |
|---|---|---|
| `fundamental` | Sector context, earnings risk, regulatory/macro overhangs | Trade direction opposes strong fundamentals |
| `technical` | Trend, distance from MA, recent vol, exhaustion signals | Buying tops or selling bottoms; signal contradicted by technicals |
| `risk` | Concentration, correlation with current book, event risk, tail asymmetry | Trade meaningfully increases portfolio risk without upside |
| `value` (Buffett-style) | FCF-yield cheapness, moats, durability, 10-year-hold question | Buying overvalued names or speculative momentum plays |

Each persona is one LLM call with a distinct system prompt. They share the same input (a `TradeProposal`) and the same output schema (an `AgentVerdict`).

### Aggregation rule (weighted veto)

```
approved iff:
  NOT risk_vetoed                      # risk persona has single-vote veto power
  AND n_veto < 2                       # at least 2 vetoes needed from other personas
  AND n_veto < (n_approve + n_annotate)  # vetoes must be outvoted overall
```

In English:
- The **risk** persona has **single-vote veto power.** One veto from `risk` blocks the trade. Justification: portfolio-level concerns (concentration, correlation, tail risk) are objectively checkable; if the risk persona flags it, the trade should not happen.
- All other personas (fundamental, technical, value) need **≥2 to agree** on a veto.
- In all cases, vetoes must outnumber non-veto verdicts.
- **Fail-open:** if zero valid verdicts come back (all LLM calls errored), the trade is approved — a flaky committee shouldn't block trading.

The risk persona's system prompt (`personas.py:_RISK_MANAGER_PROMPT`) is explicit about this weight: it must veto only for **concrete, measurable risk** — never for "feels weak" or "low conviction" (that's the technical persona's job). Misusing the single-vote veto for non-portfolio concerns is the failure mode to watch for.

Persona names returned by the LLM are normalised to the canonical set (`fundamental`, `technical`, `risk`, `value`) inside `committee.py:_ask_one_persona` — the LLM tends to invent variants like `buffett_value_investor` or `risk-manager`, and we overwrite to keep aggregation lookups stable.

### Why sells pass through unchanged

When the committee is wired into `m3_live_alpaca.py --with-committee`, only **buys** go through review. Sells (closing positions) skip the committee entirely. Reason: if we vetoed a sell + approved a buy, the portfolio would grow past `topk`, breaking the strategy invariant. Pragmatically: closing a position is risk-reducing; we always allow risk reduction.

A future refinement could pair sells with their replacement buys and veto-or-approve them together — left for later if real evidence emerges that we're closing names we shouldn't.

## Run

### Standalone dry-run (no Alpaca, no real orders)

The fastest way to see what the committee thinks of today's signals:

```bash
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml

# Pin a specific signal date in the CSV
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml --signal-date 2024-01-15

# Provide current holdings to see realistic rotation decisions
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml \
    --current-holdings AAPL,AMGN,AMZN,AXP,CAT,GS,JPM,MCD,PG,UNH

# Use OpenAI instead of DeepSeek
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml --provider openai
```

Output shows every proposed trade, each persona's verdict + rationale, and an end-of-run summary with veto rate and cost. Cost: roughly **$0.005-0.01 per trade reviewed** (4 LLM calls, ~500-1000 tokens each, DeepSeek pricing).

### Live runner with committee

```bash
# Dry-run with committee — see proposed orders and committee verdicts but submit nothing
python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml --dry-run --force --with-committee

# Real paper run with committee in the path
python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml --with-committee
```

Vetoed trades are logged in the run record under `committee_decisions[]` but **never submitted**. The run record JSON now includes one entry per reviewed trade with the deciding rationale and each persona's individual verdict — full audit trail.

## Sanity check: veto rate

After ≥100 reviewed trades, plot the per-persona veto rate. Expectations:

| Persona | Healthy veto rate |
|---|---|
| fundamental | 5-15% |
| technical | 10-20% |
| risk | 5-15% |
| value | 5-25% (more conservative) |

If any persona shows <1% veto rate, it's noise — remove or rewrite the prompt. If any shows >50% veto rate, the prompt is too aggressive — soften the rules.

The committee-aggregate veto rate (post-aggregation) should land in 5-20%. Higher means the brain is producing too many bad ideas; lower means the committee isn't catching anything (or the brain is great — unlikely for vanilla LightGBM+Alpha158).

## Cost budget

Per trade reviewed: ~$0.005-0.010 with DeepSeek (4 personas × ~$0.0015 each).

For a daily DOW-30 paper test (~3 buys/day, 5 trading days):
- ~15 trades × $0.008 = **~$0.12 for a full 5-day paper test**

The committee is the cheapest layer in the project. The bottleneck is wall-clock — 4 parallel API calls × 5-15 seconds each = ~15s per trade. For a daily rebalance with 3 buys that's ~45s of committee time, irrelevant.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `DEEPSEEK_API_KEY not set` | `.env.m2` not loaded | Auto-loaded by the scripts; ensure the file exists |
| `ValidationError` on LLM response | Persona returned malformed JSON | Lowers `committee.temperature`; bad responses are dropped silently (fail-open) |
| Veto rate 0% across many trades | Personas always approve | Tighten the `_BASE_RULES` in `personas.py` — explicitly require "veto rate of 5-25%" |
| Veto rate 100% across many trades | Personas always veto (often happens with reasoning models trying to be helpful) | Lower temperature; emphasise APPROVE as default |
| `Persona failed: <err>` warnings during normal runs | LLM provider rate-limited or transient | Built-in: failed verdicts are excluded from the count, aggregate uses remaining valid ones |
| All buys vetoed → no rotation | Either prompts too strict OR market is genuinely in a bad spot | Inspect the rationales in `logs/m3_live/.../X.json`; if reasoning is sound, that's the committee working as intended |

## Early empirical results (2026-06-04)

First end-to-end exercises of the committee. Sample is small (9 trades reviewed) and only one signal date — directional, not conclusive.

### Buys reviewed (committee path that goes to live trading)

| Source | Symbols | Verdicts | Aggregate |
| --- | --- | --- | --- |
| Cold-start dry-run (10 buys) | AAPL, AMGN, AMZN, AXP, CAT, GS, JPM, MCD, PG, UNH | All approved | 0% veto rate |
| Synthetic rotation (3 buys) | MCD, PG, UNH | All approved | 0% veto rate |
| Live paper run (3 buys) | CVX, TRV, V | All approved | 0% veto rate |
| **Buy total (16)** | — | **0 vetoed** | **0% veto rate** |

Persona-level: technical analyst vetoed several buys individually (overbought / near-52w-high concerns), value vetoed one (CAT @ 35x earnings), but never reached the ≥2-veto threshold. Risk persona always approved on these trades because none materially worsened concentration or correlation given the portfolio context provided.

### Sells reviewed (standalone CLI only; sells skip committee in live)

The synthetic rotation also ran 3 sells through the standalone CLI for observation:

| Symbol | Verdicts | Outcome |
| --- | --- | --- |
| KO | 1 approve / 3 veto | VETOED |
| MSFT | 1 approve / 3 veto | VETOED |
| V | 0 approve / 4 veto | VETOED |

**This 100% sell-veto rate is a calibration finding, not a live-trading problem.** The personas evaluate each sell as "should we exit this position on its merits?" and predictably find quality reasons to hold a Dow 30 name (strong cloud growth, defensive moat, etc.). They do not understand the TopkDropoutStrategy invariant that *something must be sold* to make room for new buys.

This is exactly why the live integration in `m3_live_alpaca.py:_run_committee` excludes sells. If we ever extend committee review to sells, the prompts will need to be aware of the rotation context (e.g., "you are evaluating whether THIS sell is worse than the alternative sells available, not whether the position is good in absolute terms").

### Total cost

~$0.012 across all 19 reviews (DeepSeek). Per-trade ~$0.001 (4 personas in parallel). The committee remains the cheapest layer in the project; latency (~2s per trade) is the only practical constraint.

## Definition of done

- ✅ M5 package scaffolded with 4 personas and shared schema.
- ✅ Standalone `m5_review_signals.py` runs without touching Alpaca.
- ✅ `m3_live_alpaca.py --with-committee` integration: vetoed buys logged + skipped.
- ✅ Live paper run with committee executed end-to-end (2026-06-04 — 3 buys approved + submitted to Alpaca, audit trail in `logs/m3_live/dow30/2026-06-04.json`).
- ⏳ 100+ trades reviewed; per-persona and aggregate veto rates filled into the sanity-check table above. Currently at 19 reviews.
- ⏳ ADR-003 updated once we have ≥100 trades and stable per-persona numbers.
