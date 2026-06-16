"""Execute a single Proposal against Qlib and return metrics.

Reuses M1's Qlib region map and the M1 `extract_metrics` function so the
numbers we get out are computed identically to the M1 lock.
"""

from __future__ import annotations

import logging
from typing import Any

from dipdiver._paths import resolve_provider_uri
from dipdiver.brain.baselines.config import BaselineConfig
from dipdiver.brain.m2.lite.schema import Factor, Metrics

log = logging.getLogger(__name__)


_QLIB_INITIALISED = False


def _init_qlib(m1: BaselineConfig) -> None:
    """Initialise Qlib once per process — repeat calls are no-ops."""
    global _QLIB_INITIALISED
    if _QLIB_INITIALISED:
        return

    from qlib.config import REG_CN, REG_US

    from dipdiver.brain.baselines._qlib.init import safe_qlib_init

    region = {"us": REG_US, "in": REG_US, "crypto": REG_US, "cn": REG_CN}.get(m1.region, REG_US)
    safe_qlib_init(
        provider_uri=str(resolve_provider_uri(m1.qlib_provider_uri)),
        region=region,
    )
    _QLIB_INITIALISED = True


def _build_task(m1: BaselineConfig, factors: list[Factor]) -> dict[str, Any]:
    """Mirror M1's task structure, swapping Alpha158 for Alpha158Plus."""
    bp = m1.backtest_params
    extra = [{"name": f.name, "expression": f.expression} for f in factors]

    handler_kwargs = {
        "start_time": m1.train_start,
        "end_time": m1.test_end,
        "fit_start_time": m1.train_start,
        "fit_end_time": m1.train_end,
        "instruments": m1.universe,
        "infer_processors": [
            {"class": "RobustZScoreNorm",
             "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        "label": ["Ref($close, -2) / Ref($close, -1) - 1"],
        "extra_factors": extra,
    }

    return {
        "model": {
            "class": "LGBModel",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": m1.model_params if m1.model == "lightgbm" else {
                "loss": "mse", "colsample_bytree": 0.8879, "learning_rate": 0.0421,
                "subsample": 0.8789, "lambda_l1": 205.6999, "lambda_l2": 580.9768,
                "max_depth": 8, "num_leaves": 210, "num_threads": 20,
            },
        },
        "dataset": {
            "class": "DatasetH",
            "module_path": "qlib.data.dataset",
            "kwargs": {
                "handler": {
                    "class": "Alpha158Plus",
                    "module_path": "dipdiver.brain.m2.lite.handler",
                    "kwargs": handler_kwargs,
                },
                "segments": {
                    "train": [m1.train_start, m1.train_end],
                    "valid": [m1.valid_start, m1.valid_end],
                    "test": [m1.test_start, m1.test_end],
                },
            },
        },
        "port_analysis": {
            "strategy": {
                "class": "TopkDropoutStrategy",
                "module_path": "qlib.contrib.strategy.signal_strategy",
                "kwargs": {"signal": "<PRED>", "topk": bp.get("topk", 10),
                           "n_drop": bp.get("n_drop", 3)},
            },
            "backtest": {
                "start_time": m1.test_start,
                "end_time": m1.test_end,
                "account": 100_000_000,
                "benchmark": m1.benchmark,
                "exchange_kwargs": {
                    "limit_threshold": 0.095, "deal_price": "close",
                    "open_cost": bp.get("open_cost", 0.0005),
                    "close_cost": bp.get("close_cost", 0.0015),
                    "min_cost": bp.get("min_cost", 5),
                },
            },
        },
    }


import re

# Textual sanity rules for common LLM-proposed expressions that Qlib will
# reject at evaluation time. Each entry: (compiled regex, reason).
#
# Qlib's actual expression parser isn't a stable public API across versions
# (parse_field has moved/been removed). Rather than depend on it, we pattern-
# match the specific failures observed in real m2-lite runs.
_KNOWN_BAD_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^\s*-(?!1\s*\*)"),
        "unary minus not supported; write `0 - x` or `-1 * x`",
    ),
    (
        re.compile(r"\bRank\s*\(\s*[^,)]+\s*\)"),
        "Rank requires a rolling-window arg: Rank(x, N)",
    ),
    (
        re.compile(r"Ref\s*\(\s*[^,)]+\s*,\s*-\s*\d+"),
        "Ref with negative N looks ahead -- banned",
    ),
]


def _drop_invalid_factors(
    m1: BaselineConfig, factors: list[Factor]
) -> tuple[list[Factor], list[tuple[str, str]]]:
    """Filter factors by static pattern checks. Returns (kept, dropped).

    Without this, a single bad LLM-proposed expression takes down the whole
    loop. Catches the failure modes we've observed in real runs; unknown
    failure modes still get through and fail at execute time (with a clearer
    error than before).
    """
    kept: list[Factor] = []
    dropped: list[tuple[str, str]] = []
    for f in factors:
        bad_reason: str | None = None
        for pattern, reason in _KNOWN_BAD_PATTERNS:
            if pattern.search(f.expression):
                bad_reason = reason
                break
        if bad_reason:
            dropped.append((f.name, bad_reason))
        else:
            kept.append(f)
    return kept, dropped


def execute(m1: BaselineConfig, factors: list[Factor], experiment_name: str) -> Metrics:
    """Build task, fit, run signal + port analysis records, return Metrics.

    Validates factors up front and drops any that fail to parse, so one bad
    LLM-generated expression doesn't kill the whole loop.
    """
    _init_qlib(m1)

    from qlib.utils import init_instance_by_config
    from qlib.workflow import R
    from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord

    from dipdiver.brain.baselines._qlib.metrics import extract_metrics

    kept, dropped = _drop_invalid_factors(m1, factors)
    if dropped:
        log.warning("execute: dropped %d invalid factor(s): %s",
                    len(dropped),
                    "; ".join(f"{n}({e[:60]})" for n, e in dropped))
    if not kept:
        raise RuntimeError(
            f"all {len(factors)} factor(s) failed validation; first error: "
            f"{dropped[0][1] if dropped else 'unknown'}"
        )

    task = _build_task(m1, kept)
    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])

    with R.start(experiment_name=experiment_name):
        model.fit(dataset)
        recorder = R.get_recorder()
        SignalRecord(model, dataset, recorder).generate()
        SigAnaRecord(recorder).generate()
        PortAnaRecord(recorder, config=task["port_analysis"]).generate()
        metrics_dict = extract_metrics(recorder)

    return Metrics(
        sharpe=metrics_dict["sharpe"],
        annualised_return=metrics_dict["annualised_return"],
        annualised_volatility=metrics_dict["annualised_volatility"],
        max_drawdown=metrics_dict["max_drawdown"],
        turnover=metrics_dict["turnover"],
        hit_rate=metrics_dict["hit_rate"],
        n_trades=metrics_dict["n_trades"],
        benchmark_annualised_return=metrics_dict["benchmark_annualised_return"],
        excess_return=metrics_dict["annualised_return"]
                       - metrics_dict["benchmark_annualised_return"],
        ic=None,                 # M1's extract_metrics doesn't expose IC; future enhancement
        rank_ic=None,
    )
