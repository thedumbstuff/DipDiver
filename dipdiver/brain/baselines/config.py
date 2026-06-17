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
    # Label engineering (M-crypto). Defaults reproduce the original next-period
    # forward-return label exactly, so existing configs/locks are unchanged; both
    # fields enter config_hash so setting them forces a fresh lock.
    label_horizon: int = 1  # forward-return horizon in periods (1 = next period)
    label_vol_normalize: bool = False  # divide the forward return by trailing vol

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

    def roll_window(self, *, cadence: str = "1y", anchor_date: str | None = None) -> "BaselineConfig":
        """Stage 3 / M10 — return a copy with train/valid/test windows shifted forward.

        Preserves each window's calendar width. `cadence` is parsed as 'Ny',
        'Nm', or 'Nd' for years/months/days. `anchor_date` (YYYY-MM-DD) ends
        the new test window; when None, the new test_end becomes today UTC.

        The result is validated by `__post_init__`, so impossible windows
        (overlap, reversed dates) raise immediately.
        """
        from datetime import date, datetime, timezone

        # Validate cadence syntax eagerly so misconfiguration is loud.
        c = cadence.strip().lower()
        if not (
            (c.endswith("y") or c.endswith("m") or c.endswith("d"))
            and c[:-1].replace(".", "", 1).isdigit()
        ):
            raise ValueError(
                f"unknown cadence {cadence!r}; expected forms like '1y', '6m', '90d'"
            )

        if anchor_date is None:
            anchor = datetime.now(timezone.utc).date()
        else:
            anchor = date.fromisoformat(anchor_date)

        # Width of each window
        def _width(s: str, e: str) -> int:
            return (date.fromisoformat(e) - date.fromisoformat(s)).days

        train_w = _width(self.train_start, self.train_end)
        valid_w = _width(self.valid_start, self.valid_end)
        test_w = _width(self.test_start, self.test_end)

        # Old test_end → new test_end
        from datetime import timedelta
        new_test_end = anchor
        new_test_start = new_test_end - timedelta(days=test_w)
        new_valid_end = new_test_start - timedelta(days=1)
        new_valid_start = new_valid_end - timedelta(days=valid_w)
        new_train_end = new_valid_start - timedelta(days=1)
        new_train_start = new_train_end - timedelta(days=train_w)

        return BaselineConfig(
            name=self.name,
            universe=self.universe,
            model=self.model,
            train_start=new_train_start.isoformat(),
            train_end=new_train_end.isoformat(),
            valid_start=new_valid_start.isoformat(),
            valid_end=new_valid_end.isoformat(),
            test_start=new_test_start.isoformat(),
            test_end=new_test_end.isoformat(),
            benchmark=self.benchmark,
            qlib_provider_uri=self.qlib_provider_uri,
            region=self.region,
            seed=self.seed,
            model_params=dict(self.model_params),
            backtest_params=dict(self.backtest_params),
        )


def load_config(path: str | Path) -> BaselineConfig:
    """Load a YAML config file into a validated BaselineConfig."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return BaselineConfig(**raw)
