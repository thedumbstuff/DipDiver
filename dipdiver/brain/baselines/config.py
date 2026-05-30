"""Baseline run configuration — loaded from YAML, validated, hashed."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dipdiver.brain.baselines.universes import get_universe


@dataclass(frozen=True)
class BaselineConfig:
    """Everything needed to reproduce a baseline run.

    The config_hash is the identity of a locked result. Any change to the fields
    below changes the hash, which forces a new lock — there is deliberately no
    notion of "compatible" configs that share a result.
    """

    name: str
    universe: str
    model: str  # "lightgbm" | "lstm"
    train_start: str  # "YYYY-MM-DD"
    train_end: str
    valid_start: str
    valid_end: str
    test_start: str
    test_end: str
    benchmark: str
    qlib_provider_uri: str
    region: str  # "us" | "cn" | "in" — passed to qlib.init
    seed: int
    model_params: dict[str, Any] = field(default_factory=dict)
    backtest_params: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.model not in ("lightgbm", "lstm"):
            raise ValueError(f"unsupported model {self.model!r}")
        get_universe(self.universe)  # raises if unknown
        for s, e in (
            (self.train_start, self.train_end),
            (self.valid_start, self.valid_end),
            (self.test_start, self.test_end),
        ):
            if s >= e:
                raise ValueError(f"date range invalid: {s} >= {e}")
        # Time-fence rule (see docs/VALIDATION.md): train < valid < test, no overlap.
        if not (self.train_end <= self.valid_start and self.valid_end <= self.test_start):
            raise ValueError("train/valid/test ranges must be ordered with no overlap")

    @property
    def config_hash(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:16]


def load_config(path: str | Path) -> BaselineConfig:
    """Load a YAML config file into a validated BaselineConfig."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return BaselineConfig(**raw)
