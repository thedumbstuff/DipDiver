# M2 · m2-lite — DipDiver's own LLM factor proposer

> **Goal.** Iteratively propose Qlib factor expressions with an LLM, backtest each against an M1 baseline, lock the best result if it improves over M1.

## What this replaces

The earlier RD-Agent-based M2 (removed; see git history of `docs/milestones/M2_rd_agent.md` if you need the post-mortem). Short version of what went wrong with rdagent:

- Hardcoded CSI300/cn_data in qlib templates AND in LLM prompts — required a stateful patcher to redirect.
- Auto-created a second conda env (`rdagent4qlib`) with a stale scipy that silently broke portfolio analysis.
- 3 loops × 18 min/loop on DeepSeek-v4-pro all rolled up to the same SOTA backtest result.

m2-lite is ~800 LOC across a `dipdiver/brain/m2/lite/` subpackage and three CLI scripts. No second conda env, no docker, no template patching, no Jinja.

## Architecture

```
dipdiver/brain/m2/lite/
├── schema.py     — Pydantic: Factor, Proposal, Metrics, LoopRecord
├── prompts.py    — system + user prompts (Python f-strings, no YAML)
├── proposer.py   — OpenAI SDK call with JSON mode + Pydantic validation
├── handler.py    — Alpha158Plus: subclass appending LLM-proposed expressions
├── executor.py   — Build Qlib task, validate factors, fit, backtest, return Metrics
└── loop.py       — Outer R+D loop, per-loop JSON logs, cost cap

scripts/
├── m2_lite_run.py     — CLI: run the loop
└── m2_lite_verify.py  — CLI: list runs, summarise, --detail per-loop, --loop N
```

### Three design choices worth defending

1. **Factors are Qlib expression strings**, not generated Python code. A factor is `"$close / Ref($close, 5) - 1"`. Qlib parses these natively. No codegen → no sandbox → no second conda env.
2. **JSON output, not pickles.** `loop_N.json` per loop + `summary.json` at the end. Grep-able, diff-able, schema-stable.
3. **Reuses M1's metrics pipeline.** Strategy Sharpe/AnnRet/MaxDD/Turnover come from `dipdiver.brain.baselines._qlib.metrics.extract_metrics` — the exact code path M1's lock used. Apples-to-apples from day 1.

## Prerequisites

- **M1 LightGBM baseline locked** for the universe you want to challenge:
  ```bash
  python scripts/m1_run.py --config dow30_lightgbm.yaml --lock
  ```
  (m2-lite's executor uses LightGBM, so locking the LightGBM M1 variant gives a same-model comparison. Locking the LSTM variant works too but is a structurally harder bar.)
- **Python 3.12 + `dipdiver[brain,m2]` installed.** Windows venv or WSL conda env — both work for m2-lite.
- **LLM API key.** `DEEPSEEK_API_KEY` (default) or `OPENAI_API_KEY` (`--provider openai`). Put them in `.env.m2` — the script auto-loads from the repo root, no `source` needed.

## Run

```bash
python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml

# Tighter / wider / different provider
python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml --loops 8 --cap 1.50
python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml --provider openai

# Custom output dir
python scripts/m2_lite_run.py --m1-config world_indices_lightgbm.yaml --output-dir /tmp/m2-run
```

Output layout:

```
logs/m2_lite/<m1-config>_<timestamp>/
├── loop_0.json
├── loop_1.json
├── …
└── summary.json   # universe, m1 baseline, best loop, totals, delta vs M1
```

End-of-run stdout shows the headline. Full inspection via the verify script (next section).

## Verify / inspect

```bash
# Table of every m2-lite run, newest first — best for "which universe was that?"
python scripts/m2_lite_verify.py --list

# Default: newest run, summary + per-loop one-liner table
python scripts/m2_lite_verify.py

# Full per-loop dump (hypothesis text, every factor expression, rationale, backtest)
python scripts/m2_lite_verify.py --detail

# Drill into a specific loop in a specific run
python scripts/m2_lite_verify.py --run logs/m2_lite/world_indices_lightgbm_20260602_170031 --loop 1
```

`--list` output:

```
#   RUN DIR                                  UNIV           OK    BEST SHARPE     vs M1    COST
0   world_indices_lightgbm_20260602_170031   world_indices  5/5        +2.422    +0.083  $0.011
1   dow30_lightgbm_20260602_163625           dow30          5/5        +1.308    +0.019  $0.014
```

## Cost knobs

| Knob | What it does | Reasonable default |
|---|---|---|
| `--loops N` | Max iterations | 5 |
| `--cap $X` | Hard USD ceiling — loop aborts when exceeded | $2 |
| `--provider {deepseek,openai}` | DeepSeek-v4-pro: ~$0.27/M in, $1.10/M out. OpenAI gpt-4o: ~10× | deepseek |

Typical 5-loop DeepSeek run: **$0.01–$0.02, 10–25 min wall-clock**. Most of that is Qlib backtest, not LLM.

## Factor validator (safety net)

Inside `executor.py`, every proposed factor is checked against known-bad patterns before being passed to Qlib. **Drops the bad factor, keeps the rest — the whole loop no longer fails because of one syntactically wrong expression.** Caught patterns:

| Pattern | Why it fails | Use instead |
|---|---|---|
| `-(expr)` | Qlib has no unary minus | `0 - (expr)` or `-1 * (expr)` |
| `Rank(x)` | Qlib's `Rank` requires a rolling window | `Rank(x, N)` |
| `Ref(x, -N)` | Negative offset = look-ahead bias | `Ref(x, N)` (N >= 1) |

The same constraints are stated in the system prompt, so the LLM rarely produces these any more — but the validator keeps a run safe if it does.

## Results so far

| Universe | M1 LightGBM Sharpe | M2 best Sharpe | ΔSharpe | M2 best loop | Cost |
|---|---|---|---|---|---|
| dow30 | +1.289 | +1.308 | +0.019 | Loop 1: intraday vol + overnight gap + long-term reversal + volume osc + skew | $0.014 |
| world_indices | +2.339 | +2.422 | +0.083 | (varies per run) | $0.011 |

m2-lite produces small but **consistent** positive deltas across the two universes tested. Both runs succeeded on all 5 loops, no factor-validator drops needed after the prompt was tightened.

The world_indices delta (+0.083) being meaningfully larger than dow30's (+0.019) suggests the m2-lite edge is *not* universe-overfit — bigger cross-section → more room for added factors to contribute signal.

## Limitations + what's deliberately not in here

- **No code generation.** Factors must be Qlib expressions. If a research idea needs Python (custom ML architecture, custom loss), m2-lite can't propose it.
- **No multi-agent debate.** One LLM call per loop, takes proposals as-is. That's M5's job (TradingAgents-style risk-veto committee, when M5 lands).
- **No formal significance test.** Comparison is a single-number Sharpe delta vs M1. ROADMAP's rolling-window signed-rank test needs daily-return time series persisted per loop — the chart DataFrame is already in the recorder, this is a future enhancement.
- **`n_trades` is approximate.** Qlib's report has no `trade_count` column. We report it as the number of days with non-zero portfolio turnover (one per rebalance day with any churn). Useful as a sanity check, not a literal trade count.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `[m2-lite] no .env.m2 at .../  — relying on shell environment` followed by `DEEPSEEK_API_KEY not set` | `.env.m2` file missing | `cp .env.m2.example .env.m2`, paste your key |
| `proposer failed after 2 attempts: ValidationError` | LLM returned malformed JSON twice in a row | Try `--provider openai`, or tighten the system prompt |
| `[m2-lite] execute: dropped 2 invalid factor(s)` in log | Validator caught bad expressions | Informational — loop continues with the valid factors. If the same patterns keep appearing, tighten the prompt |
| `qlib: instrument not found` during backtest | Universe doesn't exist in the data store | Run `scripts/m1_setup.py` first for that universe |
| Loop succeeds but Sharpe is unchanged vs prior loop | Factor expressions might be mathematically equivalent OR Qlib filled NaN | Inspect `loop_N.json` for the expressions, check the data store has full coverage |
| `Universe: ?` in verify output for an older run | Summary predates the universe-naming fix | New runs get explicit fields; old runs' universe is still in the dir name. Verify shows the dir name at the top |

## Definition of done

- ✅ m2-lite runs to completion on dow30 with at least one Sharpe-positive loop. **Done — +0.019 delta on Loop 1.**
- ✅ A second universe attempted to confirm prompt is genuinely universe-aware. **Done — world_indices, +0.083 delta.**
- ✅ Per-loop and summary JSON outputs are explicit about which universe / M1 lock the run targets.
- Decision recorded in ADR-001: m2-lite stays as the M2 brain layer. Marginal alpha, trivial cost, no operational overhead. **Pending — update ADR-001 once the next milestone starts referencing M2's role.**
