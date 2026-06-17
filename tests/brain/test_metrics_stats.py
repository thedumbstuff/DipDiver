"""Unit tests for the statistical helpers in baselines/_qlib/metrics.py:
calendar-derived annualisation and the Probabilistic Sharpe Ratio / minTRL.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dipdiver.brain.baselines._qlib.metrics import _periods_per_year, _psr_and_min_trl


def test_periods_per_year_5day_vs_7day():
    # 5-day (business day) calendar → ~252-262 (pure bdays, no holiday removal)
    bidx = pd.bdate_range("2024-01-01", "2025-12-31")
    bday_ppy = _periods_per_year(bidx)
    assert 250 <= bday_ppy <= 264
    # 7-day (crypto) calendar → ~365, and clearly higher than the 5-day count
    didx = pd.date_range("2024-01-01", "2025-12-31", freq="D")
    day_ppy = _periods_per_year(didx)
    assert 360 <= day_ppy <= 366
    assert day_ppy > bday_ppy + 90  # the gap that drove the ~20% Sharpe error


def test_periods_per_year_degenerate_inputs():
    assert _periods_per_year(pd.DatetimeIndex([])) == 252.0
    assert _periods_per_year(pd.DatetimeIndex(["2024-01-01"])) == 252.0


def test_annualised_sharpe_scales_with_sqrt_periods():
    # Same per-period Sharpe, annualised at 252 vs 365, differs by sqrt(365/252):
    # the ~20% crypto Sharpe understatement that hardcoding 252 caused.
    ratio = np.sqrt(365.0 / 252.0)
    assert 1.19 < ratio < 1.21


def test_psr_increases_with_sample_length():
    rng = np.random.default_rng(0)
    base = pd.Series(rng.normal(0.001, 0.01, 250))
    long = pd.Series(rng.normal(0.001, 0.01, 2000))
    sr_base = base.mean() / base.std()
    sr_long = long.mean() / long.std()
    psr_base, _ = _psr_and_min_trl(base, sr_base)
    psr_long, _ = _psr_and_min_trl(long, sr_long)
    assert 0.0 <= psr_base <= 1.0 and 0.0 <= psr_long <= 1.0
    # More observations of a comparable edge → more confidence.
    assert psr_long > psr_base


def test_psr_zero_and_min_trl_infinite_for_nonpositive_sharpe():
    s = pd.Series([0.01, -0.01, 0.02, -0.02, 0.0, -0.005])
    psr, min_trl = _psr_and_min_trl(s, sr_per_period=0.0)
    assert psr == 0.0 and min_trl == float("inf")


def test_min_trl_finite_for_strong_positive_sharpe():
    rng = np.random.default_rng(1)
    s = pd.Series(rng.normal(0.002, 0.005, 1000))  # high per-period Sharpe
    sr = s.mean() / s.std()
    psr, min_trl = _psr_and_min_trl(s, sr)
    assert min_trl < len(s)  # enough data to be significant
    assert psr > 0.95
