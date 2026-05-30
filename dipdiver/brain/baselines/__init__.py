"""M1 — Qlib baselines.

Reproducible, boring baselines per (universe, model). Locked results from these
runs are the comparator for every later improvement claim in the stack.

See docs/milestones/M1_qlib_baseline.md for the recipe and acceptance criteria.
"""

from dipdiver.brain.baselines.config import BaselineConfig, load_config
from dipdiver.brain.baselines.results import BaselineResult, load_locked, save_locked
from dipdiver.brain.baselines.universes import CRYPTO_BASKET, DOW30, NIFTY50, Universe

__all__ = [
    "BaselineConfig",
    "BaselineResult",
    "CRYPTO_BASKET",
    "DOW30",
    "NIFTY50",
    "Universe",
    "load_config",
    "load_locked",
    "save_locked",
]
