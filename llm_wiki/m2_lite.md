# M2-lite — LLM Factor Proposer (Brain)

## Purpose

**M2-lite** is DipDiver's factor discovery engine. It runs an iterative loop:
1. **Propose** — call an LLM (DeepSeek or OpenAI) with M1 baseline context
2. **Validate** — reject factors that violate Qlib syntax rules (unary minus, look-ahead, etc.)
3. **Execute** — backtest proposed factors on Qlib, compute Sharpe/return/metrics
4. **Record** — save loop results, learn from prior attempts, propose next factors

The LLM chain is purely in-process Python — no RD-Agent, no Docker, no second conda env. All state is JSON on disk, atomic writes to `loop_N.json` as each loop completes.

**Replaced:** The earlier RD-Agent-based M2 (see git history of `docs/milestones/M2_rd_agent.md`). RD-Agent hardcoded CSI300/cn_data in qlib templates, auto-created a stale conda env (`rdagent4qlib`), and required Jinja-patching to redirect paths. m2-lite avoids these entirely.

## Entry Points & Execution

### Run the loop

```bash
# Standard 5-loop DeepSeek run
python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml

# Tunable loops / cost cap / provider
python scripts/m2_lite_run.py --m1-config world_indices_lightgbm.yaml --loops 8 --cap 1.50 --provider openai
```

**Script:** `scripts/m2_lite_run.py` (lines 47–110)
- Parses CLI: `--m1-config`, `--loops` (default 5), `--cap` (default $2.00), `--provider` (default "deepseek")
- Auto-loads `.env.m2` from repo root if it exists (lines 24–44)
- Calls `run_lite_loop(m1, output_dir, max_loops, cost_cap_usd, provider)` from `dipdiver.brain.m2.lite.loop`

**Output structure:**
```
logs/m2_lite/<m1-config-stem>_<YYYYMMDD_HHMMSS>/
├── loop_0.json       # First loop's LoopRecord (JSON)
├── loop_1.json       # ...
├── ...
└── summary.json      # Final summary: best loop, deltas vs M1, total cost
```

### Inspect results

```bash
# List all m2-lite runs, newest first (universe + best Sharpe + delta + cost)
python scripts/m2_lite_verify.py --list

# Summarise newest run (M1 baseline + best loop metrics + hypothesis + factors)
python scripts/m2_lite_verify.py

# Full per-loop text dump (hypothesis + factors + rationale + backtest)
python scripts/m2_lite_verify.py --detail

# Drill into one specific loop
python scripts/m2_lite_verify.py --loop 2
```

**Script:** `scripts/m2_lite_verify.py` (lines 190–245)
- `--list`: table of all runs (lines 148–174)
- `--run <dir>`: inspect a specific run; default: newest under `logs/m2_lite/`
- `--loop N`: print full record for loop index N (lines 110–145)
- `--detail`: append full per-loop dumps after summary (line 237)

## Core Components

### 1. Schema & Validation (`schema.py`)

**Pydantic models enforce contracts between LLM and our code.**

#### `Factor` (lines 12–25)
```python
class Factor(BaseModel):
    name: str  # snake_case, 1–40 chars, pattern ^[a-z][a-z0-9_]*$
    expression: str  # Qlib expression, 1–400 chars
    rationale: str | None  # one-sentence why this should predict returns
```

#### `Proposal` (lines 28–40)
```python
class Proposal(BaseModel):
    hypothesis: str  # one paragraph trading intuition + market regime (10–2000 chars)
    market_thesis: str | None  # optional broader context (≤1500 chars)
    factors: list[Factor]  # 1–5 candidate expressions
```

#### `Metrics` (lines 43–56)
Strategy backtest results (post-cost), computed identically to M1 via `extract_metrics`:
- `sharpe`, `annualised_return`, `annualised_volatility`, `max_drawdown`
- `turnover`, `hit_rate`, `n_trades`
- `benchmark_annualised_return`, `excess_return`
- `ic`, `rank_ic` (optional; not yet populated)

#### `LoopRecord` (lines 59–67)
Atomic per-loop snapshot:
```python
class LoopRecord(BaseModel):
    index: int
    proposal: Proposal | None  # None if propose() itself failed
    metrics: Metrics | None    # None if backtest failed
    error: str | None          # populated on any failure
    llm_input_tokens, llm_output_tokens: int  # for cost tracking
    llm_cost_usd: float
    wall_seconds: float
```

### 2. Prompt Templates (`prompts.py`)

**Two static prompts for reasoning models (Claude Opus, GPT-4, DeepSeek-v4-pro).**

#### `SYSTEM_PROMPT` (lines 17–79)
- Frames the role: quantitative researcher, propose factor expressions for cross-sectional next-day returns
- Specifies output format: single JSON matching `Proposal` schema
- **Qlib expression rules** (lines 34–62):
  - Available fields: `$open`, `$high`, `$low`, `$close`, `$volume`, `$factor`, `$vwap`
  - Operators: `+`, `-`, `*`, `/`, `Ref(x, N)`, `Mean`, `Std`, `Sum`, `Max`, `Min`, `Skew`, `Kurt`, `Rank(x, N)`, `Abs`, `Sign`, `Log`, `If`, `Greater`, `Less`
  - **Pitfalls explicitly warned** (lines 54–62): no unary minus, Rank needs window, no look-ahead refs, no cross-sectional ranking inside expressions
- Quality bar: "Be specific. Round 3+ should address what you learned from prior backtests."

#### `render_user_prompt(...)` (lines 82–129)
Per-loop context injected into user message:
- Universe, region, train/test windows, benchmark (lines 95–102)
- M1 baseline Sharpe/return as challenge bar
- **Prior rounds history** (lines 106–128): for each loop, show hypothesis, factor names/expressions, and backtest result (Sharpe, AnnRet, MaxDD, IC)
- Directive to avoid repeating prior factors and address learned lessons

#### `proposal_schema_json()` (lines 136–138)
Returns the `Proposal` schema in JSON form for the LLM to consume directly.

### 3. LLM Proposer (`proposer.py`)

**Single function: take context, return a validated Proposal via OpenAI SDK with retry logic.**

#### `ProposerConfig` (lines 37–45)
Dataclass specifying LLM endpoint and cost params:
```python
@dataclass(frozen=True)
class ProposerConfig:
    model: str  # e.g. "deepseek-v4-pro"
    base_url: str  # API endpoint (DeepSeek, OpenAI, etc.)
    api_key_env: str  # env var name holding the key
    pricing_tier: str  # key into _PROVIDER_PRICING for cost calc
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout_seconds: int = 180
```

#### `propose(cfg, *, universe, region, ..., prior_loops) → tuple[Proposal, int, int, float, float]` (lines 62–134)
**Main entry point for LLM calls.**

Process:
1. Build user prompt via `render_user_prompt(...)`
2. Construct messages: `[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]`
3. Call `client.chat.completions.create(response_format={"type": "json_object"})` twice on failure:
   - **First attempt** (line 97–120): parse response JSON, validate with `Proposal.model_validate(raw)`, return
   - **Validation failure** (lines 121–132): append bad response + error to messages, ask for correction, retry once
4. **Returns** `(proposal, in_tokens, out_tokens, cost_usd, wall_seconds)`

Cost calculation via `_cost(input_tokens, output_tokens, tier)` (lines 55–59):
- DeepSeek: $0.27/M in, $1.10/M out
- OpenAI GPT-4o: $2.50/M in, $10/M out
- Anthropic (Sonnet/Opus) prices also hardcoded

### 4. Factor Executor (`executor.py`)

**Build a Qlib task, validate expressions, fit LightGBM, backtest, return Metrics.**

#### Initialization: `_init_qlib(m1)` (lines 22–36)
- One-time init per process (global `_QLIB_INITIALISED` flag)
- Maps region to Qlib's region enum (lines 31): `{"us": REG_US, "in": REG_US, "crypto": REG_US, "cn": REG_CN}`

#### Task building: `_build_task(m1, factors) → dict` (lines 39–109)
Mirrors M1's Qlib task structure, with LLM-proposed factors injected:
- **Handler** (`Alpha158Plus`, see section 5): Alpha158 base + extra factors appended
- **Label**: `["Ref($close, -2) / Ref($close, -1) - 1"]` (next-day return)
- **Processors**:
  - Inference: `RobustZScoreNorm` (feature group, clip outliers), `Fillna`
  - Learn: `DropnaLabel`, `CSRankNorm` (cross-sectional rank normalize labels)
- **Model**: LGBModel with M1's hyperparams (or defaults if M1 is LSTM)
- **Strategy**: TopkDropoutStrategy, backtest with spread costs

#### Pattern validator: `_drop_invalid_factors(m1, factors) → (kept, dropped)` (lines 136–158)
**Safety net — drops syntactically invalid expressions without killing the loop.**

Static regex checks for known-bad patterns (lines 120–133):
1. `r"^\s*-(?!1\s*\*)"` — unary minus (not `-1 *`) → suggest `0 - x` or `-1 * x`
2. `r"\bRank\s*\(\s*[^,)]+\s*\)"` — `Rank(x)` without window → require `Rank(x, N)`
3. `r"Ref\s*\(\s*[^,)]+\s*,\s*-\s*\d+"` — look-ahead refs → rejected

Returns `(kept_factors, dropped_as_list_of_tuples)`. If all factors dropped, raises `RuntimeError`.

#### Main execution: `execute(m1, factors, experiment_name) → Metrics` (lines 161–211)
1. Validate + drop bad factors (line 175)
2. Build task (line 186)
3. Init model and dataset via Qlib's `init_instance_by_config` (lines 187–188)
4. **Under `R.start(experiment_name=...)`**:
   - Fit LightGBM (line 191)
   - Generate signals via `SignalRecord` (line 193)
   - Analyse signals via `SigAnaRecord` (line 194)
   - Run portfolio backtest via `PortAnaRecord` (line 195)
5. Extract metrics via M1's `extract_metrics(recorder)` (line 196)
6. Construct and return `Metrics` object (lines 198–211)

**Key detail:** `ic` and `rank_ic` are set to `None` — M1's `extract_metrics` doesn't expose them yet (line 209 comment).

### 5. Handler: Alpha158Plus (`handler.py`)

**Qlib data handler that appends LLM-proposed expressions to Alpha158's base 158 features.**

#### `Alpha158Plus(Alpha158)` (lines 17–38)
Subclass of Qlib's standard `Alpha158` handler.

```python
def __init__(self, *args, extra_factors: list[dict] | None = None, **kwargs):
    self._extra_factors = list(extra_factors or [])  # Store BEFORE super().__init__
    super().__init__(*args, **kwargs)  # Triggers get_feature_config

def get_feature_config(self) -> tuple[list[str], list[str]]:
    exprs, names = super().get_feature_config()  # Alpha158's 158 expressions
    for f in self._extra_factors:
        exprs.append(f["expression"])
        names.append(f["name"])
    return exprs, names
```

**Design note:** Store `_extra_factors` *before* calling `super().__init__` because Alpha158 calls `get_feature_config` during init. The extra expressions are evaluated alongside Alpha158's base, so the dataset has `158 + len(extra_factors)` columns.

### 6. Main Loop (`loop.py`)

**Orchestrate propose → execute → persist across multiple iterations.**

#### Provider registry: `PROVIDERS` (lines 25–38)
```python
PROVIDERS: dict[str, ProposerConfig] = {
    "deepseek": ProposerConfig(
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        pricing_tier="deepseek",
    ),
    "openai": ProposerConfig(
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        pricing_tier="openai_gpt4o",
    ),
}
```

#### Main loop: `run_lite_loop(m1, output_dir, max_loops=5, cost_cap_usd=5.0, provider="deepseek") → dict` (lines 41–119)

Process:
1. **Load M1 baseline** via `load_locked(m1.config_hash)` (line 59) — get Sharpe and return to use as challenge bar
2. **For each loop `idx` in range(max_loops)** (lines 67–116):
   - **Cost check** (lines 68–70): abort if `total_cost >= cost_cap_usd`
   - **Propose** (lines 76–93):
     - Call `propose(proposer_cfg, universe=..., region=..., ..., prior_loops=loops)` (line 77)
     - Catch exceptions → record error, persist, break
   - **Execute** (lines 95–103):
     - Call `execute(m1, proposal.factors, experiment_name=f"m2_lite_loop_{idx}")` (line 97)
     - Catch exceptions → set `err` instead of metrics
   - **Persist** (lines 105–111):
     - Create `LoopRecord(index, proposal, metrics, error, tokens, cost, wall_time)` (lines 105–109)
     - Call `_persist_loop(output_dir, rec)` → write `loop_{idx}.json` (line 110)
     - Append to `loops` list; accumulate `total_cost` (lines 111–112)
3. **Generate summary** via `_persist_summary(...)` (line 118)

#### Persistence helpers:

**`_persist_loop(output_dir, rec)`** (lines 122–124)
```python
path = output_dir / f"loop_{rec.index}.json"
path.write_text(rec.model_dump_json(indent=2), encoding="utf-8")
```

**`_persist_summary(output_dir, m1, loops, m1_sharpe, m1_ann_return, total_cost) → dict`** (lines 127–176)
Builds and persists `summary.json`:
```json
{
  "timestamp_utc": "2026-06-04T...",
  "m1_config_name": "dow30_lightgbm",
  "universe": "dow30",
  "region": "us",
  "benchmark": "^GSPC",
  "test_window": {"start": "2023-01-01", "end": "2024-12-31"},
  "m1_baseline": {"sharpe": 1.289, "annualised_return": 0.12, "config_hash": "abc123..."},
  "n_loops_run": 5,
  "n_loops_successful": 5,
  "total_cost_usd": 0.014,
  "total_input_tokens": 12345,
  "total_output_tokens": 5678,
  "best_loop_index": 1,
  "best": {
    "sharpe": 1.308,
    "annualised_return": 0.121,
    "max_drawdown": -0.15,
    "excess_return": 0.005,
    "delta_sharpe_vs_m1": 0.019,
    "delta_ann_return_vs_m1": 0.001,
    "factors": [{"name": "intraday_vol", "expression": "..."}],
    "hypothesis": "..."
  }
}
```

## Data Flow

### Per-loop execution walkthrough:

```
1. CLI invokes run_lite_loop(m1_config, output_dir, max_loops=5, cost_cap_usd=$2)
   ↓
2. Load M1 baseline metrics (Sharpe, return) from locked result
   ↓
3. FOR loop_idx IN range(max_loops):
   ↓
   3a. PROPOSE:
       - Build user prompt with universe, train/test windows, M1 metrics, prior loop results
       - Call LLM API (DeepSeek/OpenAI) with system prompt + user context
       - Validate JSON response against Proposal schema (retry once on error)
       - Extract (proposal, in_tokens, out_tokens, cost_usd, wall_seconds)
   ↓
   3b. VALIDATE FACTORS:
       - Regex check each factor expression for known-bad patterns
       - Drop invalid expressions; keep valid ones
       - If all dropped, raise RuntimeError (loop fails)
   ↓
   3c. EXECUTE:
       - Build Qlib task with LLM factors injected via Alpha158Plus
       - Fit LightGBM on train window
       - Generate signals, analyse, backtest on test window
       - Extract metrics (Sharpe, return, volatility, max_dd, etc.)
   ↓
   3d. RECORD:
       - Wrap proposal, metrics, tokens, cost into LoopRecord
       - Write loop_{idx}.json atomically
       - Append to in-memory loops list
       - Accumulate cost
   ↓
4. Generate summary.json:
   - Find best loop by Sharpe
   - Compute deltas vs M1 baseline
   - Write summary with M1 context, best loop details, total cost

5. Return summary dict to CLI for stdout report
```

### Cost tracking:

Each loop's cost is computed via:
```
cost_usd = (in_tokens / 1e6) * in_price_per_M + (out_tokens / 1e6) * out_price_per_M
```

Hardcoded rates in `proposer.py` lines 27–34. Total accumulated cost is checked against `cost_cap_usd` before each loop (line 68).

## Known Issues & Gotchas

### Factor validation is pattern-based, not semantic
- Catches the three most common LLM failure modes (unary minus, Rank without window, look-ahead Ref)
- Unknown failure modes still slip through and fail at Qlib parse time
- If a bad pattern keeps appearing, tighten the system prompt

### No IC/RankIC exposure yet
- `Metrics.ic` and `Metrics.rank_ic` are always `None` (executor.py line 209)
- M1's `extract_metrics` doesn't expose them; would require adding fields to the recorder
- Future enhancement

### n_trades is approximate
- Qlib's report has no `trade_count` column
- We report it as the number of days with non-zero portfolio turnover (one per rebalance day with churn)
- Useful for sanity checks but not a literal trade count

### Temperature 0.7 for "reasoning" models
- DeepSeek-v4-pro, Claude Opus, and GPT-4 are treated as reasoning models with `temperature=0.7` (mid-range)
- No switching to `temperature=1.0` for sampling diversity or `temperature=0.2` for focus
- Hard-coded in `ProposerConfig` (line 43)

### Cost cap is per-run, not per-loop
- Once `total_cost >= cost_cap_usd`, the loop aborts *at the start of the next iteration*
- A loop already in progress is never cancelled mid-way
- Cost is rounded to 4 decimal places in the summary (line 154)

## Performance & Cost

**Typical 5-loop DeepSeek run:** $0.01–$0.02, 10–25 min wall-clock
- DeepSeek LLM call: ~1–2 min
- Qlib backtest per loop: ~2–5 min (depends on universe size, train window length)
- Bottleneck: backtest execution, not LLM

**Cost per loop (DeepSeek-v4-pro):**
- Prompt + prior loops history: ~1k–3k input tokens → ~$0.0003–$0.001
- Proposal (hypothesis + 3–5 factors + rationales): ~200–500 output tokens → ~$0.0002–$0.0005
- **Per-loop LLM cost: ~$0.0005–$0.0015**
- 5 loops: ~$0.0025–$0.0075

## Testing & Verification

**Observed results (as of early 2026):**
- **dow30 (LightGBM):** M1 Sharpe +1.289 → M2 best +1.308 (Δ +0.019) — Loop 1, cost $0.014
  - Factors: intraday vol, overnight gap, long-term reversal, volume oscillator, skew
- **world_indices (LightGBM):** M1 Sharpe +2.339 → M2 best +2.422 (Δ +0.083) — varies per run, cost $0.011

**Consistency:** Both runs succeeded on all 5 loops, no validator drops after prompt tightening.

**Interpretation:** Larger cross-section (world_indices vs dow30) → more room for added signal → larger delta. Suggests the edge is not universe-overfit.

## Limitations (By Design)

- **No code generation.** Factors must be Qlib expressions; no custom ML architectures or loss functions.
- **No multi-agent debate.** One LLM call per loop; proposals taken as-is. (That's M5's job: TradingAgents-style risk-veto committee.)
- **No formal significance test.** Comparison is single-number Sharpe delta. ROADMAP will add rolling-window signed-rank test.
- **Fixed to LightGBM.** Executor always uses LightGBM. LSTM runs work but are structurally harder to beat (higher bar).

## Cross-references

- **[M1 baselines](m1_baselines.md)** — M1's locked baseline, metrics extraction, Qlib task structure that m2-lite reuses
- **[M3 execution](m3_execution.md)** — downstream execution engine that will consume M2's best factors
- **[M5 committee](m5_committee.md)** — multi-agent debate framework that sits above M2's proposals for live trades
- **[Validation](validation.md)** — anti-overfit rules and the lock-comparator gate that any proposed factor must beat
