"""Translate a BaselineConfig into a Qlib task dict.

The dict shape matches Qlib's reference workflow YAMLs in
qlib/examples/benchmarks/{LightGBM,LSTM}/workflow_config_*.yaml so that the
init_instance_by_config machinery picks up the right classes.
"""

from __future__ import annotations

from typing import Any

from dipdiver.brain.baselines.config import BaselineConfig


# Default next-period forward return (h=1): Ref($close,-2)/Ref($close,-1)-1.
_LABEL = ["Ref($close, -2) / Ref($close, -1) - 1"]


def _label_exprs(config: BaselineConfig) -> list[str]:
    """Build the label expression from the config.

    Forward return over `label_horizon` periods, measured from the next bar
    (t+1) so there's no look-ahead at decision time:
        Ref($close, -1-h) / Ref($close, -1) - 1
    h=1 reproduces the original `_LABEL` exactly. When `label_vol_normalize` is
    set, the return is divided by trailing realised vol (past-only window) so the
    target is comparable across volatility regimes — useful for noisy crypto.
    """
    h = int(getattr(config, "label_horizon", 1) or 1)
    fwd = f"Ref($close, {-1 - h}) / Ref($close, -1) - 1"
    if getattr(config, "label_vol_normalize", False):
        win = max(h * 4, 20)
        # Trailing daily-return vol up to t (uses past closes only).
        vol = f"Std($close / Ref($close, 1) - 1, {win}) + 1e-12"
        return [f"({fwd}) / ({vol})"]
    return [fwd]


def _data_handler_config(config: BaselineConfig) -> dict[str, Any]:
    return {
        "start_time": config.train_start,
        "end_time": config.test_end,
        "fit_start_time": config.train_start,
        "fit_end_time": config.train_end,
        "instruments": config.universe,
        "infer_processors": [
            {"class": "RobustZScoreNorm",
             "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        "label": _label_exprs(config),
    }


def _model_block(config: BaselineConfig) -> dict[str, Any]:
    if config.model == "lightgbm":
        return {
            "class": "LGBModel",
            "module_path": "qlib.contrib.model.gbdt",
            "kwargs": config.model_params,
        }
    if config.model == "lstm":
        return {
            "class": "LSTM",
            "module_path": "qlib.contrib.model.pytorch_lstm",
            "kwargs": config.model_params,
        }
    raise ValueError(f"unsupported model {config.model!r}")


def _dataset_block(config: BaselineConfig) -> dict[str, Any]:
    # Both LightGBM and qlib.contrib.model.pytorch_lstm.LSTM use DatasetH.
    # (TSDatasetH is for pytorch_lstm_ts.LSTM, which we are not using.)
    handler = {
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
        "kwargs": _data_handler_config(config),
    }
    segments = {
        "train": [config.train_start, config.train_end],
        "valid": [config.valid_start, config.valid_end],
        "test": [config.test_start, config.test_end],
    }
    return {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {"handler": handler, "segments": segments},
    }


def _port_analysis_config(config: BaselineConfig) -> dict[str, Any]:
    bp = config.backtest_params
    return {
        "strategy": {
            "class": "TopkDropoutStrategy",
            "module_path": "qlib.contrib.strategy.signal_strategy",
            "kwargs": {
                "signal": "<PRED>",
                "topk": bp.get("topk", 10),
                "n_drop": bp.get("n_drop", 3),
                # Minimum holding days before a name is eligible to be dropped.
                # >1 curbs turnover (and the cost drag that was sinking crypto).
                "hold_thresh": bp.get("hold_thresh", 1),
            },
        },
        "backtest": {
            "start_time": config.test_start,
            "end_time": config.test_end,
            "account": 100_000_000,
            "benchmark": config.benchmark,
            "exchange_kwargs": {
                "limit_threshold": 0.095,
                "deal_price": "close",
                "open_cost": bp.get("open_cost", 0.0005),
                "close_cost": bp.get("close_cost", 0.0015),
                "min_cost": bp.get("min_cost", 5),
            },
        },
    }


def build_task(config: BaselineConfig) -> dict[str, Any]:
    return {
        "model": _model_block(config),
        "dataset": _dataset_block(config),
        "port_analysis": _port_analysis_config(config),
    }
