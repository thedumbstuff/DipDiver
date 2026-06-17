"""Pull headline metrics out of a Qlib recorder after a workflow run.

PortAnaRecord writes `portfolio_analysis/report_normal_1day.pkl` and
`indicator_analysis/indicators_normal_1day.pkl`. We read the former for
return/risk and derive the rest.

Annualisation is derived from the *actual* return calendar, not a hardcoded
252: a 7-day market (crypto) has ~365 observations/year and a 5-day market
(equities/indices) ~252. Hardcoding 252 understated a crypto Sharpe by
~sqrt(365/252) ≈ 1.20x. See `_periods_per_year`.

We also report the Probabilistic Sharpe Ratio (PSR) and the Minimum Track
Record Length (minTRL) from Bailey & López de Prado — these say whether a
Sharpe is statistically distinguishable from zero given the sample length and
the return distribution's skew/kurtosis, which a raw Sharpe point estimate
hides. PSR is gated on alongside Sharpe so a marginal model stays a candidate.
"""

from __future__ import annotations

import math
from typing import Any


def _periods_per_year(index) -> float:
    """Infer observations-per-year from a return series' DatetimeIndex.

    Derived from the span so it auto-adapts: crypto (7-day) → ~365, equities
    (5-day) → ~252, and any future weekly/intraday resampling. Clamped to a
    sane daily band so a tiny sample or a degenerate index can't produce an
    absurd factor.
    """
    n = len(index)
    if n < 2:
        return 252.0
    try:
        span_days = (index[-1] - index[0]).days
    except Exception:  # noqa: BLE001 — non-datetime index, fall back
        return 252.0
    if span_days <= 0:
        return 252.0
    ppy = n / (span_days / 365.25)
    return float(min(max(ppy, 1.0), 366.0))


def _psr_and_min_trl(
    returns, sr_per_period: float, *, benchmark_sr: float = 0.0, conf: float = 0.95
) -> tuple[float, float]:
    """Probabilistic Sharpe Ratio and Minimum Track Record Length.

    PSR(c) = NormCDF( (SR - c) * sqrt(T-1) / sqrt(1 - g3*SR + ((g4-1)/4)*SR^2) )

    with SR the *per-period* Sharpe (same frequency as the benchmark c), g3 the
    skew and g4 the (non-excess) kurtosis of the returns, and T the number of
    observations. minTRL is the T at which PSR would reach `conf` against c.
    Returns (psr, min_trl); min_trl is +inf when SR <= c (never reaches conf).
    Ref: Bailey & Lopez de Prado, "The Sharpe Ratio Efficient Frontier" (2012).
    """
    from scipy.stats import kurtosis, norm, skew

    x = returns.to_numpy()
    t = len(x)
    if t < 3 or sr_per_period == 0.0:
        return 0.0, float("inf")
    g3 = float(skew(x, bias=False))
    g4 = float(kurtosis(x, fisher=False, bias=False))  # non-excess: normal → 3
    sr = float(sr_per_period)
    denom = 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr * sr
    if denom <= 0:
        return 0.0, float("inf")
    psr = float(norm.cdf((sr - benchmark_sr) * math.sqrt(t - 1) / math.sqrt(denom)))
    edge = sr - benchmark_sr
    if edge <= 0:
        return psr, float("inf")
    z = float(norm.ppf(conf))
    min_trl = float(1.0 + denom * (z / edge) ** 2)
    return psr, min_trl


def extract_metrics(recorder: Any, benchmark_label: str = "bench") -> dict[str, Any]:
    """Return the dict shape BaselineResult expects."""
    import numpy as np
    import pandas as pd

    report = recorder.load_object("portfolio_analysis/report_normal_1day.pkl")
    assert isinstance(report, pd.DataFrame), f"unexpected report type: {type(report)}"

    # Strategy daily return after costs.
    ret = report["return"].dropna()
    cost = report.get("cost", pd.Series(0.0, index=ret.index)).reindex(ret.index).fillna(0.0)
    net = ret - cost

    bench = report.get(benchmark_label, report.get("bench", report.get("benchmark")))
    if bench is None:
        bench = pd.Series(0.0, index=net.index)
    bench = bench.reindex(net.index).fillna(0.0)

    # Annualisation factor from the actual calendar (crypto ≈365, equities ≈252).
    ann = _periods_per_year(net.index)

    mean = float(net.mean())
    std = float(net.std())
    per_period_sharpe = mean / std if std > 0 else 0.0

    ann_return = mean * ann
    ann_vol = std * np.sqrt(ann)
    sharpe = per_period_sharpe * float(np.sqrt(ann)) if std > 0 else 0.0

    # Sortino — uses downside deviation only (volatility from losing days).
    downside = net.where(net < 0, 0.0)
    downside_vol = float(downside.std() * np.sqrt(ann))
    sortino = float(ann_return / downside_vol) if downside_vol > 0 else 0.0

    equity = (1 + net).cumprod()
    drawdown = (equity / equity.cummax() - 1).min()
    max_dd = float(drawdown) if pd.notna(drawdown) else 0.0

    # Calmar = annualised return / |max drawdown|. Standard quant tearsheet metric.
    calmar = float(ann_return / abs(max_dd)) if max_dd != 0 else 0.0

    hit_rate = float((net > 0).mean()) if len(net) else 0.0

    # Statistical confidence in the Sharpe given sample length + non-normality.
    psr, min_trl = _psr_and_min_trl(net, per_period_sharpe)

    # Approx turnover: sum of absolute weight changes per day, annualised, per-side.
    turnover = float(report.get("turnover", pd.Series(0.0, index=net.index)).mean() * ann)

    # qlib's report has no "trade_count" column. As a meaningful proxy, count
    # the number of days with any non-trivial portfolio turnover — each such
    # day involves at least one buy/sell pair under TopkDropoutStrategy.
    turnover_series = (
        report.get("turnover", pd.Series(0.0, index=ret.index))
        .reindex(ret.index)
        .fillna(0.0)
    )
    n_trades = int((turnover_series > 1e-8).sum())

    bench_ann_return = float(bench.mean() * ann)

    return {
        "annualised_return": ann_return,
        "annualised_volatility": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "max_drawdown": max_dd,
        "hit_rate": hit_rate,
        "turnover": turnover,
        "n_trades": n_trades,
        "benchmark_annualised_return": bench_ann_return,
        "periods_per_year": ann,
        "psr": psr,
        "min_trl": min_trl,
    }
