"""Baseline result schema — the M1 acceptance artefact.

A locked BaselineResult is the comparator every later improvement must beat.
Locks are immutable; new evidence creates a new lock with a new config_hash.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

LOCKED_DIR = Path(__file__).parent / "locked"


@dataclass(frozen=True)
class BaselineResult:
    config_hash: str
    config_name: str
    universe: str
    model: str
    test_start: str
    test_end: str
    # Headline metrics. All cost-adjusted.
    annualised_return: float
    annualised_volatility: float
    sharpe: float
    max_drawdown: float  # negative
    hit_rate: float  # fraction of profitable trades
    turnover: float  # annualised, per-side
    n_trades: int
    benchmark_annualised_return: float
    excess_return: float  # annualised_return - benchmark_annualised_return
    # Provenance.
    qlib_version: str
    git_sha: str  # commit at run time
    run_timestamp_utc: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _locked_path(config_hash: str) -> Path:
    return LOCKED_DIR / f"{config_hash}.json"


def save_locked(result: BaselineResult) -> Path:
    """Write a locked result. Refuses to overwrite an existing lock."""
    path = _locked_path(result.config_hash)
    if path.exists():
        raise FileExistsError(
            f"locked result already exists at {path}; "
            "delete it explicitly if you intend to relock"
        )
    LOCKED_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def load_locked(config_hash: str) -> BaselineResult:
    path = _locked_path(config_hash)
    if not path.exists():
        raise FileNotFoundError(f"no locked result for {config_hash}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return BaselineResult(**data)


def compare(current: BaselineResult, locked: BaselineResult, tolerance: float = 0.05) -> bool:
    """True iff headline metrics are within `tolerance` (fractional) of the lock.

    Used by the M1 acceptance test: a fresh run on the same config_hash must
    reproduce the locked numbers within ±5% (default).
    """
    if current.config_hash != locked.config_hash:
        raise ValueError("config_hash mismatch — these are not the same run")
    fields = (
        "annualised_return",
        "annualised_volatility",
        "sharpe",
        "max_drawdown",
        "hit_rate",
        "turnover",
    )
    for f in fields:
        a, b = getattr(current, f), getattr(locked, f)
        denom = max(abs(b), 1e-9)
        if abs(a - b) / denom > tolerance:
            return False
    return True
