"""m2-lite — DipDiver's own LLM factor-proposer.

A ~400-line replacement for RD-Agent's qlib_rd_loop. Same problem shape
(propose factor → backtest → iterate) without the operational pain of
hardcoded paths, hidden conda envs, Jinja-mixed YAML, or zero-cost reports.
"""

from dipdiver.brain.m2.lite.loop import PROVIDERS, run_lite_loop
from dipdiver.brain.m2.lite.proposer import ProposerConfig, propose
from dipdiver.brain.m2.lite.schema import Factor, LoopRecord, Metrics, Proposal

__all__ = [
    "Factor",
    "LoopRecord",
    "Metrics",
    "PROVIDERS",
    "Proposal",
    "ProposerConfig",
    "propose",
    "run_lite_loop",
]
