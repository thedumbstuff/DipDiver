"""Typed contracts between the committee LLMs and our orchestration code.

Each persona agent reads a TradeProposal and returns an AgentVerdict.
The committee aggregates verdicts into a CommitteeDecision (veto-only:
the committee can block a trade or pass it through, never enlarge).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Direction = Literal["buy", "sell"]


class TradeProposal(BaseModel):
    """One proposed trade for the committee to review."""

    symbol: str = Field(min_length=1, max_length=20)
    direction: Direction
    universe: str          # "dow30", "world_indices", etc.
    benchmark: str         # e.g. "DJI"
    universe_description: str  # one-line description for LLM context
    signal_score: float        # raw model score (higher = stronger long signal)
    signal_date: str           # ISO date the signal was generated
    notional_usd: float        # planned trade size
    current_holdings: list[str]  # other names in the portfolio right now
    test_window: str           # e.g. "2024-01-01 -> 2025-12-31" for context


class AgentVerdict(BaseModel):
    """One persona's verdict on a TradeProposal."""

    persona: str           # which persona produced this verdict
    decision: Literal["approve", "veto", "annotate"] = Field(
        description="`veto` blocks the trade; `annotate` logs a concern but lets it through; "
                    "`approve` is a clean pass."
    )
    rationale: str = Field(
        min_length=10, max_length=800,
        description="One paragraph explaining the verdict. A human auditor "
                    "should be able to understand the agent's reasoning."
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="0..1, the agent's confidence in its decision. Used as a tiebreaker.",
    )


class CommitteeDecision(BaseModel):
    """Aggregated verdict across all persona agents."""

    proposal: TradeProposal
    verdicts: list[AgentVerdict]
    approved: bool         # final outcome — committee allows the trade
    n_approve: int
    n_veto: int
    n_annotate: int
    cost_usd: float = 0.0
    in_tokens: int = 0
    out_tokens: int = 0

    @property
    def majority_rationale(self) -> str:
        """A short summary string for the run log: just the deciding rationale."""
        if self.approved:
            approving = [v for v in self.verdicts if v.decision == "approve"]
            if approving:
                return f"approved by {len(approving)}/{len(self.verdicts)}: " + approving[0].rationale[:200]
            return "approved by default (no objections)"
        vetoing = [v for v in self.verdicts if v.decision == "veto"]
        if vetoing:
            return f"vetoed by {len(vetoing)}/{len(self.verdicts)}: " + vetoing[0].rationale[:200]
        return "vetoed (reason unknown)"
