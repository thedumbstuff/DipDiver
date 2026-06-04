"""M5 - Risk-veto committee.

Multi-agent LLM debate sits between M1/M2 signal proposals and M3 execution.
Reviews each proposed trade through several distinct lenses (fundamental,
technical, risk-manager, value-investor). Can veto a trade but cannot
create or enlarge one - it is a one-way filter.

See docs/milestones/M5_committee.md for the architecture rationale and
ADR-003 in docs/STACK_DECISIONS.md for the veto-only semantics.
"""

from dipdiver.brain.m5.committee import CommitteeConfig, review
from dipdiver.brain.m5.personas import PERSONAS, Persona
from dipdiver.brain.m5.schema import (
    AgentVerdict,
    CommitteeDecision,
    Direction,
    TradeProposal,
)

__all__ = [
    "AgentVerdict",
    "CommitteeConfig",
    "CommitteeDecision",
    "Direction",
    "PERSONAS",
    "Persona",
    "TradeProposal",
    "review",
]
