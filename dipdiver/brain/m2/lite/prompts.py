"""Prompt templates as plain Python strings.

Two prompts:
  SYSTEM_PROMPT   — universe-aware framing, Qlib expression rules, output schema
  USER_PROMPT(...) — per-loop context (M1 baseline, prior attempts + results)

Designed for reasoning models (DeepSeek v4-pro, Claude Opus, GPT-4.x).
"""

from __future__ import annotations

import json

from dipdiver.brain.m2.lite.schema import LoopRecord, Proposal


SYSTEM_PROMPT = """\
You are a quantitative researcher. Your job is to propose factor expressions
that predict next-day cross-sectional returns for the universe specified by
the user. You will go through several rounds; each round you propose new
factors and you'll be shown the realised backtest results so you can iterate.

OUTPUT FORMAT:
  Return a single JSON object matching the Proposal schema:
    {
      "hypothesis": "<one paragraph trading intuition>",
      "market_thesis": "<optional, broader>",
      "factors": [
        {"name": "<snake_case>", "expression": "<qlib expression>",
         "rationale": "<one sentence>"}
      ]
    }

QLIB EXPRESSION LANGUAGE — strict rules:
  Fields available per instrument per day:
      $open, $high, $low, $close, $volume, $factor, $vwap
  Allowed operators / functions (every N MUST be a positive integer literal):
      + - * /                — binary arithmetic, scalar or series
      Ref(x, N)              — value N days ago (N >= 1)
      Mean(x, N) / Std(x, N) — N-day rolling mean / std
      Sum(x, N)              — N-day rolling sum
      Max(x, N) / Min(x, N)  — N-day rolling max / min
      Skew(x, N) / Kurt(x, N)
      Rank(x, N)             — rolling rank within own N-day history (N is REQUIRED)
      Abs(x), Sign(x), Log(x)
      If(cond, a, b)         — element-wise
      Greater(a, b), Less(a, b)
  Examples that work:
      5-day momentum:        $close / Ref($close, 5) - 1
      Volume spike:          $volume / Mean($volume, 20) - 1
      20d realised vol:      Std($close / Ref($close, 1) - 1, 20)
      Vol-scaled momentum:   ($close / Ref($close, 5) - 1) / Std($close / Ref($close, 1) - 1, 20)

PITFALLS — these will fail to parse:
  - UNARY MINUS does not exist. To negate, write `0 - x` or `-1 * x`.
       BAD:   -($close / Ref($close, 5) - 1)
       GOOD:  0 - ($close / Ref($close, 5) - 1)
       GOOD:  -1 * ($close / Ref($close, 5) - 1)
  - `Rank(x)` without a window will error. Use `Rank(x, N)`.
  - Cross-sectional ranking happens later in the processor pipeline — DO NOT
    try to rank across instruments inside the expression.
  - No nested function dictionaries, no curly braces, no Python comprehensions.

DO NOT:
  - Reference instruments other than the current one.
  - Use Python syntax (no `if/else`, no list comprehensions, no `**`).
  - Generate expressions that look ahead (no Ref with negative N).
  - Propose the same factor that was already evaluated in a prior round.

CONSTRAINTS:
  - factor names must be snake_case, start with a letter, <=40 chars.
  - propose between 1 and 5 factors per round.
  - factors should be diverse — don't propose three flavours of the same idea.

QUALITY BAR:
  Be specific. "5-day momentum" is fine for round one but by round three you
  should propose something that addresses what you LEARNED from earlier
  backtests — drawdown patterns, low IC, high turnover, etc.
"""


def render_user_prompt(
    universe: str,
    region: str,
    train_start: str,
    test_start: str,
    test_end: str,
    benchmark: str,
    m1_sharpe: float,
    m1_ann_return: float,
    prior_loops: list[LoopRecord],
) -> str:
    """Build the per-loop user prompt with full history of prior attempts."""
    lines = [
        f"UNIVERSE:        {universe}  (region: {region})",
        f"TRAINING WINDOW: {train_start} -> {test_start}",
        f"TEST WINDOW:     {test_start} -> {test_end}",
        f"BENCHMARK:       {benchmark}",
        f"M1 BASELINE on test window: Sharpe={m1_sharpe:+.3f}, "
        f"Ann return={m1_ann_return:+.2%}",
        "",
    ]
    if not prior_loops:
        lines.append("This is round 1. Propose your first set of factors.")
    else:
        lines.append("PRIOR ROUNDS:")
        for r in prior_loops:
            lines.append(f"  Round {r.index}:")
            lines.append(f"    hypothesis: {_short(r.proposal.hypothesis, 200)}")
            lines.append(
                "    factors: "
                + ", ".join(f"{f.name}=({f.expression})" for f in r.proposal.factors)
            )
            if r.metrics:
                lines.append(
                    f"    result: Sharpe={r.metrics.sharpe:+.3f}, "
                    f"AnnRet={r.metrics.annualised_return:+.2%}, "
                    f"MaxDD={r.metrics.max_drawdown:+.2%}, "
                    f"IC={r.metrics.ic if r.metrics.ic is not None else 'n/a'}"
                )
            else:
                lines.append(f"    result: FAILED ({r.error})")
        lines.append("")
        lines.append(
            "Propose your next round of factors. Address what you learned: "
            "low IC, high turnover, drawdown spikes, or just to explore a "
            "different intuition. Avoid repeating any factor already tried."
        )
    return "\n".join(lines)


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 3] + "..."


def proposal_schema_json() -> str:
    """JSON schema string for the Proposal — sent to the LLM in JSON mode."""
    return json.dumps(Proposal.model_json_schema(), indent=2)
