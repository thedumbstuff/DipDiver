"""Pull headline metrics out of a Qlib recorder after a workflow run.

PortAnaRecord writes `portfolio_analysis/report_normal_1day.pkl` and
`indicator_analysis/indicators_normal_1day.pkl`. We read the former for
return/risk and derive the rest.
"""

from __future__ import annotations

from typing import Any


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

    ann = 252  # trading days; for crypto pass-through gives a rough scaling (good enough for M1)
    ann_return = float(net.mean() * ann)
    ann_vol = float(net.std() * np.sqrt(ann))
    sharpe = float(ann_return / ann_vol) if ann_vol > 0 else 0.0

    equity = (1 + net).cumprod()
    drawdown = (equity / equity.cummax() - 1).min()
    max_dd = float(drawdown) if pd.notna(drawdown) else 0.0

    hit_rate = float((net > 0).mean()) if len(net) else 0.0

    # Approx turnover: sum of absolute weight changes per day, annualised, per-side.
    turnover = float(report.get("turnover", pd.Series(0.0, index=net.index)).mean() * ann)

    n_trades = int(report.get("trade_count", pd.Series(0, index=net.index)).sum())

    bench_ann_return = float(bench.mean() * ann)

    return {
        "annualised_return": ann_return,
        "annualised_volatility": ann_vol,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "hit_rate": hit_rate,
        "turnover": turnover,
        "n_trades": n_trades,
        "benchmark_annualised_return": bench_ann_return,
    }
