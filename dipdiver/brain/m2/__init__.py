"""M2 — LLM-driven factor discovery on top of M1's Qlib baseline.

Implemented entirely under `dipdiver.brain.m2.lite`. The earlier rdagent-based
approach was removed after empirical evaluation showed it added more
operational complexity than research value on our universes.

See docs/milestones/M2_lite.md for the recipe.
"""

from dipdiver.brain.m2.lite import (  # noqa: F401
    PROVIDERS,
    Factor,
    LoopRecord,
    Metrics,
    Proposal,
    ProposerConfig,
    propose,
    run_lite_loop,
)
