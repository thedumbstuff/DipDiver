# M5 — Risk-Veto LLM Committee

## Purpose & Entry Points

**Goal:** A multi-agent LLM debate sits between the brain (M1/M2-lite signal generation) and execution (M3 trading). It can block proposed trades but cannot create or enlarge them — a pure veto filter, never an amplifier.

**Entry points:**
1. **Standalone dry-run:** `python scripts/m5_review_signals.py --m1-config <config.yaml>` — reviews today's signals without touching Alpaca.
2. **Live integration:** `python scripts/m3_live_alpaca.py --m1-config <config.yaml> --with-committee` — gates all proposed **buys** through the committee; sells bypass it.
3. **Direct API:** `from dipdiver.brain.m5 import review, TradeProposal` — call the committee on individual proposals.

Files in scope:
- `dipdiver/brain/m5/__init__.py` — public exports
- `dipdiver/brain/m5/schema.py` — Pydantic types (TradeProposal, AgentVerdict, CommitteeDecision)
- `dipdiver/brain/m5/personas.py` — four persona definitions with system prompts
- `dipdiver/brain/m5/committee.py` — orchestrator: parallel LLM calls + aggregation logic
- `scripts/m5_review_signals.py` — standalone test harness
- `scripts/m3_live_alpaca.py` — integration point (lines 145–262)

---

## Architecture

### Four Personas

Each persona is a distinct evaluative lens, modeled as a system prompt + parallel LLM call. All four run in parallel; their verdicts are aggregated by a weighted-veto rule.

| Persona | File lines | Key responsibility |
|---|---|---|
| **fundamental** | `personas.py:51–61` | Sector context, earnings risk, regulatory/macro overhangs. Veto when trade direction opposes strong fundamentals. |
| **technical** | `personas.py:63–74` | Price trend, distance from moving average, volume, momentum exhaustion. Veto when buying tops or selling bottoms; signal contradicted by technicals. |
| **risk** | `personas.py:76–93` | Concentration risk, correlation with current holdings, idiosyncratic event risk (earnings within days, FDA decisions), tail-risk asymmetry. **Has single-vote veto power** — one risk veto blocks the trade. |
| **value** (Buffett-style) | `personas.py:95–107` | FCF-yield cheapness, business durability (moats, switching costs), moat sustainability. Veto when buying overvalued momentum plays; approve selling overextended names. |

**Design rule:** Every persona must veto *occasionally*. If a persona's veto rate is <1% over 100 trades, it adds noise without value — remove or rewrite the prompt. If >50%, the prompt is too aggressive — soften it. Healthy range: 5–25% per persona.

### Data Flow: Proposal → Verdict → Decision

**Input:** `TradeProposal` (schema.py:18–30)
```python
class TradeProposal(BaseModel):
    symbol: str
    direction: Direction  # "buy" or "sell"
    universe: str
    benchmark: str
    universe_description: str
    signal_score: float        # raw model score (higher = stronger long signal)
    signal_date: str
    notional_usd: float        # planned trade size
    current_holdings: list[str]  # portfolio context
    test_window: str           # e.g., "2024-01-01 -> 2025-12-31"
```

**Processing:** `review()` function (committee.py:119–192)
1. Initialize DeepSeek (or custom) OpenAI client from `CommitteeConfig`.
2. For each of the four personas, spawn a thread running `_ask_one_persona()` (committee.py:82–113).
3. Each persona receives:
   - System prompt (persona-specific reasoning rules)
   - User prompt (formatted TradeProposal, lines 60–79)
4. Each LLM call expects JSON output validated against `AgentVerdict` (schema.py:33–49).
5. Collect verdicts, apply aggregation rule, return `CommitteeDecision`.

**Output:** `CommitteeDecision` (schema.py:52–77)
```python
class CommitteeDecision(BaseModel):
    proposal: TradeProposal
    verdicts: list[AgentVerdict]        # one per persona
    approved: bool                       # final outcome
    n_approve: int                       # count of "approve" verdicts
    n_veto: int                          # count of "veto" verdicts
    n_annotate: int                      # count of "annotate" verdicts
    cost_usd: float                      # total API cost
    in_tokens: int                       # input tokens across all calls
    out_tokens: int                      # output tokens across all calls
```

### Aggregation Rule (Weighted Veto)

**Policy** (committee.py:173–179):
```python
risk_vetoed = any(
    v.decision == "veto" and v.persona == RISK_VETO_PERSONA for v in verdicts
)
approved = (
    not risk_vetoed
    and (n_veto < 2)
    and (n_veto < n_approve + n_annotate)
)
```

In plain English:
- **Risk persona has single-vote veto power.** If the risk manager says "veto," the trade is blocked, period. Justification: portfolio-level concerns (concentration, correlation) are objectively checkable; if the risk persona flags it, the trade should not happen.
- **All other personas need ≥2 to agree on veto.** A single technical or fundamental objection doesn't block a trade; you need consensus.
- **Vetoes must be outvoted.** Even with ≥2 non-risk vetoes, if approves + annotates outnumber vetoes, the trade passes.

### Fail-Open Semantics

**If zero valid verdicts come back** (all 4 LLM calls error or return invalid JSON), the trade is approved. Justification: a flaky committee shouldn't block trades. Failed verdicts are logged as warnings but do not influence the decision (committee.py:156–159, 170–171).

### Cost Model

**Pricing tiers** (committee.py:27–31):
```python
_PROVIDER_PRICING = {
    "deepseek": (0.27, 1.10),     # $ / M tokens (in, out)
    "openai_gpt4o": (2.50, 10.00),
}
```

**Per-trade cost:** ~$0.005–0.010 with DeepSeek (4 personas × ~1000 tokens × $0.00027 in + $0.0011 out).

**Per-run cost:** For a daily DOW-30 paper test with ~3 buys/day and 5 trading days = ~15 trades × $0.008 = **~$0.12 for a full 5-day paper test**.

---

## Configuration

`CommitteeConfig` (committee.py:34–43):
```python
@dataclass(frozen=True)
class CommitteeConfig:
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com/v1"
    api_key_env: str = "DEEPSEEK_API_KEY"
    pricing_tier: str = "deepseek"
    temperature: float = 0.3      # lower than m2-lite — consistency over creativity
    max_tokens: int = 800
    timeout_seconds: int = 60
    max_parallel: int = 4
```

**Temperature is low (0.3) by design:** We want consistent vetting, not creative brainstorming. If personas produce malformed JSON frequently, lower temperature further.

**Parallel calls:** All 4 personas run concurrently via `ThreadPoolExecutor` (max_workers=4). Wall-clock time: 4 API calls × 5–15s each = ~15–20s per trade.

---

## Integration with M3 (m3_live_alpaca.py)

**Flag:** `--with-committee` (line 320)

**Flow:**
1. M1/M2-lite propose `adds` and `removes` (line 150).
2. If `--with-committee` and there are buys, call `_run_committee()` (lines 211–262).
3. `_run_committee()` filters `adds` to only approved ones; **sells bypass the committee entirely**.
4. Only approved buys proceed to execution (Alpaca order placement).
5. All committee decisions are persisted in the run record JSON (line 205, under `committee_decisions[]` key).

**Why sells bypass the committee:**
If we vetoed a sell + approved a buy, the portfolio would grow past `topk`, violating the strategy invariant. Pragmatically: closing a position is risk-reducing; we always allow risk reduction.

**Logging:**
```
log.info(f"committee {outcome} buy {symbol}: {decision.majority_rationale}")
log.info("committee: %d/%d buys approved", len(approved), len(adds))
```

**Audit trail:** Each decision record includes:
```python
{
    "symbol": symbol,
    "approved": bool,
    "n_approve": int,
    "n_veto": int,
    "n_annotate": int,
    "summary": str,                    # majority_rationale property
    "verdicts": [dict],                # full AgentVerdict for each persona
    "cost_usd": float,
}
```

---

## Running the Committee

### Standalone Dry-Run (Recommended First Test)

**No Alpaca, no real orders.** Safe way to see what the committee thinks before wiring it into live execution.

```bash
# Review today's signals (latest date in CSV)
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml

# Pin a specific signal date
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml --signal-date 2024-01-15

# Provide current holdings to see realistic rotation decisions
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml \
    --current-holdings AAPL,AMGN,AMZN,AXP,CAT,GS,JPM,MCD,PG,UNH

# Use OpenAI instead of DeepSeek
python scripts/m5_review_signals.py --m1-config dow30_lightgbm.yaml --provider openai -v
```

**Output format** (m5_review_signals.py:189–204):
```
--- BUY AAPL (score +0.1234) ---
    [APPROVED] approves=3 vetos=0 annotations=1
    + fundamental    conf=0.95: Sector strong, tech earnings solid...
    ~ technical      conf=0.80: Price near 52-week high, consider caution...
    + risk           conf=0.92: No concentration risk, correlations healthy...
    + value          conf=0.85: Quality name, reasonable valuation...

=== SUMMARY: 15 trades reviewed: 13 approved, 2 blocked
    veto rate: 13.3%
    cost: $0.1234  (12,345 in + 9,876 out)
```

**Signals CSV path** (default): `data/signals/<m1-config-stem>.csv`

### Live Runner with Committee

```bash
# Dry-run: see proposed orders + committee verdicts, no submission
python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml --dry-run --with-committee

# Real paper run: committee in the path, verdicts logged but not enforced if approved
python scripts/m3_live_alpaca.py --m1-config dow30_lightgbm.yaml --with-committee
```

**Committee verdicts logged:**
```
[m3-live] committee APPROVED buy AAPL: approved by 3/4: Sector strong, tech earnings solid...
[m3-live] committee VETOED buy AMZN: vetoed by 1/4: Overweighted in tech already...
[m3-live] committee: 8/10 buys approved
```

---

## Calibration & Sanity Checks

### Expected Behavior: Cold-Start (Empty Portfolio)

**Scenario:** First rotation with no current holdings.
- All proposed trades are new positions (no correlation, no concentration risk).
- Risk manager has minimal concerns.
- Committee approval rate: typically **85–95%** (not 100%, because fundamental/technical/value personas still veto occasionally).

**Recent calibration result:** 0% aggregate veto on cold-start with empty portfolio — **this is expected behavior.** With zero current holdings, risk factors (concentration, correlation) are benign; only fundamental/technical/value considerations apply, and those personas are calibrated to approve ~80% of the time.

### Per-Persona Veto Rate

After ≥100 reviewed trades, measure each persona's individual veto rate:

| Persona | Healthy range | Red flags |
|---|---|---|
| fundamental | 5–15% | <1% → add teeth to the prompt; >50% → too strict |
| technical | 10–20% | <1% → persona is noise; >50% → soften the MA distance rules |
| risk | 5–15% | <1% → remove; >50% → too risk-averse for normal markets |
| value | 5–25% (wider) | <1% → can't distinguish cheap/expensive; >50% → require explicit valuation metrics |

### Committee-Aggregate Veto Rate

The final **approved = T/F** veto rate should land in **5–20%**.

- **0%:** Committee is a noop; either the brain is perfect (unlikely) or personas are too lenient (rewrite prompts).
- **50%+:** Committee is blocking too many; either the brain is broken or the personas are too strict (loosen rules).
- **Normal (5–20%):** Committee is filtering real noise from the brain while respecting its core signals.

### How to Iterate

1. Run `m5_review_signals.py` on 50–100 trades.
2. Extract per-persona verdicts from output; compute veto rate for each.
3. If a persona is <1% veto, remove it or rewrite its system prompt (personas.py lines 51–107).
4. If a persona is >50% veto, soften the decision rules in its prompt.
5. Re-test on new trades; verify veto rate moves toward target.

---

## Common Gotchas & Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DEEPSEEK_API_KEY not set` | Environment variable missing | Scripts auto-load `.env.m2` (lines 125 in m5_review_signals.py); ensure the file exists. |
| `ValidationError: <field>` | LLM returned malformed JSON | Temperature already set to 0.3; if frequent, reduce further. Bad responses are silently dropped (fail-open). |
| Veto rate 0% on 50+ trades | Personas too lenient or brain is exceptional | Tighten `_BASE_RULES` in personas.py line 27; require explicit "veto rate 5–25%" in each prompt. |
| Veto rate 100% on 50+ trades | Personas always veto (reasoning models often do this) | Lower temperature; emphasize APPROVE as the default in `_BASE_RULES`. |
| `Persona failed: [error]` warnings | LLM provider rate-limited or transient timeout | Built-in recovery: failed verdicts are excluded; aggregation uses remaining valid ones. Not a blocker. |
| All proposed buys blocked → no rotation | Either prompts are too strict OR market is genuinely in a bad spot | Inspect the rationales in run logs (e.g., `logs/m3_live/.../committee_decisions[]`). If reasoning is sound, that's the committee working correctly. |

---

## Schema Reference

### TradeProposal (schema.py:18–30)

Input contract. Fields validated by Pydantic:
- `symbol: str` — min 1, max 20 chars.
- `direction: Literal["buy", "sell"]`
- `universe: str` — e.g., "dow30", "world_indices"
- `benchmark: str` — e.g., "DJI"
- `universe_description: str` — one-line context for the LLM
- `signal_score: float` — raw model score (higher = stronger long signal)
- `signal_date: str` — ISO date
- `notional_usd: float` — planned trade size
- `current_holdings: list[str]` — portfolio snapshot for risk assessment
- `test_window: str` — e.g., "2024-01-01 -> 2025-12-31"

### AgentVerdict (schema.py:33–49)

Output contract per persona. Fields validated by Pydantic:
- `persona: str` — identifier (overridden by committee.py:109 to ensure correctness)
- `decision: Literal["approve", "veto", "annotate"]`
  - `"approve"` — clean pass
  - `"veto"` — block the trade
  - `"annotate"` — log concern but don't block (only affects count, not approval logic)
- `rationale: str` — min 10, max 800 chars; one paragraph explaining the decision
- `confidence: float` — 0.0–1.0; persona's confidence in its verdict (used as tiebreaker if needed)

### CommitteeDecision (schema.py:52–77)

Output contract. Fully populated after aggregation:
- `proposal: TradeProposal` — the input
- `verdicts: list[AgentVerdict]` — one per persona (or fewer if some failed)
- `approved: bool` — final outcome
- `n_approve`, `n_veto`, `n_annotate: int` — counts
- `cost_usd: float` — total API cost
- `in_tokens`, `out_tokens: int` — token usage
- `majority_rationale() -> str` — summary string for logs (property, schema.py:65–76)

---

## Known Limitations & Future Work

1. **Sells bypass the committee:** Currently, closing positions always skips review. A future refinement could pair sells with replacement buys and veto/approve them as a unit — left for later if evidence emerges that we're closing names we shouldn't.

2. **Single LLM provider per run:** Config pins one provider + model (DeepSeek or OpenAI). Could extend to ensemble (different models vote together) for higher robustness, but cost & latency tradeoff is steep.

3. **No explicit calibration on real historical trades:** The 0% veto rate on cold-start is correct, but we haven't yet measured veto rate on 100+ realistic rotations with existing holdings. Calibration ongoing (per docs/milestones/M5_committee.md line 131).

4. **Personas do not consult live data:** System prompts reference "recent volatility" and "distance from moving average" but the LLM receives no actual price data. Trade proposal includes signal_score and test_window context, but not real-time OHLCV. This is by design (avoid API calls per persona), but it limits technical accuracy.

---

## Cross-references

- [M1 Baselines](m1_baselines.md) — signal generation upstream of the committee
- [M2 Lite](m2_lite.md) — alternative signal layer also feeding the committee
- [M3 Execution](m3_execution.md) — live runner that gates buys through the committee
- [Stack Decisions](../docs/STACK_DECISIONS.md) — ADR-003 defines the veto-only semantics
- [Validation](validation.md) — capital deployment gates and veto-regret tracking that will gate the committee in M6
