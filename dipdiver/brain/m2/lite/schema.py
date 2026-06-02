"""Typed contracts between the LLM and our code.

The LLM is told its response must validate against `Proposal`. Pydantic
enforces this on the way in; we never feed an unvalidated string into Qlib.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Factor(BaseModel):
    name: str = Field(
        min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$",
        description="snake_case identifier used as the feature column name",
    )
    expression: str = Field(
        min_length=1, max_length=400,
        description="A valid Qlib expression, e.g. '$close / Ref($close, 5) - 1'. "
                    "Reference fields with $open $high $low $close $volume $factor $vwap.",
    )
    rationale: str | None = Field(
        default=None, max_length=400,
        description="One-sentence why this expression should predict next-day returns.",
    )


class Proposal(BaseModel):
    hypothesis: str = Field(
        min_length=10, max_length=2000,
        description="One paragraph stating the trading intuition + which market regime it targets.",
    )
    market_thesis: str | None = Field(
        default=None, max_length=1500,
        description="Optional broader thesis about the universe + period.",
    )
    factors: list[Factor] = Field(
        min_length=1, max_length=5,
        description="One to five candidate factor expressions to evaluate.",
    )


class Metrics(BaseModel):
    """Strategy-own (post-cost) metrics, computed M1-style from daily returns."""

    sharpe: float
    annualised_return: float
    annualised_volatility: float
    max_drawdown: float
    turnover: float
    hit_rate: float
    n_trades: int
    benchmark_annualised_return: float
    excess_return: float       # annualised_return - benchmark_annualised_return
    ic: float | None = None
    rank_ic: float | None = None


class LoopRecord(BaseModel):
    index: int
    proposal: Proposal | None = None            # None if propose() itself failed
    metrics: Metrics | None = None              # None if backtest failed
    error: str | None = None                    # populated on any failure
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cost_usd: float = 0.0
    wall_seconds: float = 0.0
