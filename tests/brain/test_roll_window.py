"""Tests for BaselineConfig.roll_window — Stage 3 / M10."""

from __future__ import annotations

from datetime import date

import pytest

from dipdiver.brain.baselines.config import BaselineConfig


def _cfg(
    train_start: str, train_end: str,
    valid_start: str, valid_end: str,
    test_start: str, test_end: str,
) -> BaselineConfig:
    return BaselineConfig(
        name="x",
        universe="dow30",
        model="lightgbm",
        train_start=train_start, train_end=train_end,
        valid_start=valid_start, valid_end=valid_end,
        test_start=test_start, test_end=test_end,
        benchmark="SPY",
        qlib_provider_uri="data/qlib/us",
        region="us",
        seed=42,
    )


def test_roll_window_preserves_widths():
    """Rolling should keep each window's calendar width."""
    base = _cfg(
        "2020-01-01", "2023-12-31",
        "2024-01-01", "2024-12-31",
        "2025-01-01", "2025-12-31",
    )
    rolled = base.roll_window(anchor_date="2026-12-31")
    train_w_base = (date.fromisoformat(base.train_end) - date.fromisoformat(base.train_start)).days
    train_w_roll = (date.fromisoformat(rolled.train_end) - date.fromisoformat(rolled.train_start)).days
    assert train_w_base == train_w_roll
    test_w_base = (date.fromisoformat(base.test_end) - date.fromisoformat(base.test_start)).days
    test_w_roll = (date.fromisoformat(rolled.test_end) - date.fromisoformat(rolled.test_start)).days
    assert test_w_base == test_w_roll
    assert rolled.test_end == "2026-12-31"


def test_roll_window_preserves_ordering_and_no_overlap():
    """The rolled config must still satisfy train<valid<test invariant."""
    base = _cfg(
        "2020-01-01", "2023-12-31",
        "2024-01-01", "2024-12-31",
        "2025-01-01", "2025-12-31",
    )
    rolled = base.roll_window(anchor_date="2027-01-31")
    # __post_init__ has already validated; assert explicitly for clarity.
    assert rolled.train_end <= rolled.valid_start
    assert rolled.valid_end <= rolled.test_start


def test_roll_window_carries_through_other_fields():
    base = _cfg(
        "2020-01-01", "2023-12-31",
        "2024-01-01", "2024-12-31",
        "2025-01-01", "2025-12-31",
    )
    rolled = base.roll_window(anchor_date="2026-12-31")
    assert rolled.universe == base.universe
    assert rolled.model == base.model
    assert rolled.benchmark == base.benchmark
    assert rolled.seed == base.seed
    # Hash must change since dates changed
    assert rolled.config_hash != base.config_hash


def test_roll_window_default_anchor_is_today():
    """When no anchor passed, test_end becomes today UTC."""
    from datetime import datetime, timezone
    base = _cfg(
        "2020-01-01", "2023-12-31",
        "2024-01-01", "2024-12-31",
        "2025-01-01", "2025-12-31",
    )
    rolled = base.roll_window()
    today = datetime.now(timezone.utc).date().isoformat()
    assert rolled.test_end == today


def test_roll_window_rejects_unparseable_cadence():
    base = _cfg(
        "2020-01-01", "2023-12-31",
        "2024-01-01", "2024-12-31",
        "2025-01-01", "2025-12-31",
    )
    with pytest.raises(ValueError):
        base.roll_window(cadence="ten years")
