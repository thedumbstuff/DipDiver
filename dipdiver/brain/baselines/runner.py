"""Run a baseline config end-to-end against Qlib.

Qlib is imported lazily so the package and its tests can be exercised without
pyqlib installed. The runner is the only module that touches Qlib directly;
everything else is plain Python.
"""

from __future__ import annotations

import datetime as dt
import logging
import random
import subprocess
from typing import Any

from dipdiver.brain.baselines.config import BaselineConfig
from dipdiver.brain.baselines.results import BaselineResult

log = logging.getLogger(__name__)


def _seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _qlib_version() -> str:
    try:
        import qlib

        return getattr(qlib, "__version__", "unknown")
    except ImportError:
        return "not-installed"


def run_baseline(config: BaselineConfig) -> BaselineResult:
    """Execute one baseline run and return its result."""
    _seed_everything(config.seed)
    log.info("baseline run: %s (hash=%s)", config.name, config.config_hash)

    metrics = _run_qlib_workflow(config)

    return BaselineResult(
        config_hash=config.config_hash,
        config_name=config.name,
        universe=config.universe,
        model=config.model,
        test_start=config.test_start,
        test_end=config.test_end,
        annualised_return=metrics["annualised_return"],
        annualised_volatility=metrics["annualised_volatility"],
        sharpe=metrics["sharpe"],
        max_drawdown=metrics["max_drawdown"],
        hit_rate=metrics["hit_rate"],
        turnover=metrics["turnover"],
        n_trades=metrics["n_trades"],
        benchmark_annualised_return=metrics["benchmark_annualised_return"],
        excess_return=metrics["annualised_return"] - metrics["benchmark_annualised_return"],
        qlib_version=_qlib_version(),
        git_sha=_git_sha(),
        run_timestamp_utc=dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        psr=metrics.get("psr", 0.0),
        min_trl=metrics.get("min_trl", 0.0),
        periods_per_year=metrics.get("periods_per_year", 252.0),
    )


def run_walkforward(
    config: BaselineConfig,
    *,
    n_folds: int = 4,
    step_days: int = 365,
    cadence: str = "1y",
) -> list[BaselineResult]:
    """Evaluate a config across several rolling test windows.

    A single train/valid/test split gives one noisy Sharpe point estimate that
    is easy to overfit to. This rolls the window back by `step_days` per fold
    (fold 0 = the config as-is) and re-runs the whole pipeline, so the caller
    gets a *distribution* of out-of-sample Sharpe to gate on instead of one
    number. Folds whose window runs off the data store are skipped with a log,
    not failed — `walkforward_summary` reports how many actually ran.
    """
    from datetime import date, timedelta

    base_end = date.fromisoformat(config.test_end)
    results: list[BaselineResult] = []
    for k in range(n_folds):
        anchor = (base_end - timedelta(days=step_days * k)).isoformat()
        try:
            rolled = config.roll_window(cadence=cadence, anchor_date=anchor)
            results.append(run_baseline(rolled))
        except Exception as e:  # noqa: BLE001 — a bad fold shouldn't kill the sweep
            log.warning("walkforward fold %d (anchor=%s) skipped: %s", k, anchor, e)
    return results


def walkforward_summary(
    results: list[BaselineResult],
    *,
    sharpe_min: float = 0.5,
    psr_min: float = 0.95,
) -> dict[str, Any]:
    """Aggregate `run_walkforward` output into a gate-able distribution.

    Returns median/min/mean Sharpe, median PSR, the fraction of folds clearing
    (sharpe_min AND psr_min), and the per-fold detail. Gate on the distribution
    (e.g. median Sharpe ≥ bar AND frac_passing ≥ 0.5) rather than a lucky fold.
    """
    import statistics

    if not results:
        return {
            "n_folds": 0, "sharpe_median": 0.0, "sharpe_min": 0.0,
            "sharpe_mean": 0.0, "psr_median": 0.0, "frac_passing": 0.0, "folds": [],
        }
    sharpes = [r.sharpe for r in results]
    psrs = [r.psr for r in results]
    n_pass = sum(1 for r in results if r.sharpe >= sharpe_min and r.psr >= psr_min)
    return {
        "n_folds": len(results),
        "sharpe_median": float(statistics.median(sharpes)),
        "sharpe_min": float(min(sharpes)),
        "sharpe_mean": float(statistics.fmean(sharpes)),
        "psr_median": float(statistics.median(psrs)),
        "frac_passing": n_pass / len(results),
        "folds": [
            {
                "test_start": r.test_start, "test_end": r.test_end,
                "sharpe": round(r.sharpe, 3), "psr": round(r.psr, 3),
                "max_drawdown": round(r.max_drawdown, 3),
            }
            for r in results
        ],
    }


def _qlib_region(region: str) -> Any:
    """Map our region code to Qlib's REG_* constant."""
    from qlib.config import REG_CN, REG_US

    # NIFTY and crypto reuse the US calendar+region defaults; M2 may revisit.
    return {"us": REG_US, "in": REG_US, "crypto": REG_US, "cn": REG_CN}.get(region, REG_US)


def _run_qlib_workflow(config: BaselineConfig) -> dict[str, Any]:
    """Invoke Qlib's workflow on the config and return headline metrics."""
    from qlib.utils import init_instance_by_config
    from qlib.workflow import R
    from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord

    from dipdiver._paths import resolve_provider_uri
    from dipdiver.brain.baselines._qlib.init import safe_qlib_init
    from dipdiver.brain.baselines._qlib.metrics import extract_metrics
    from dipdiver.brain.baselines._qlib.task import build_task

    safe_qlib_init(
        provider_uri=str(resolve_provider_uri(config.qlib_provider_uri)),
        region=_qlib_region(config.region),
    )

    task = build_task(config)
    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])

    with R.start(experiment_name=config.name, recorder_name=config.config_hash):
        model.fit(dataset)
        recorder = R.get_recorder()
        recorder.save_objects(**{"params.pkl": dict(config_hash=config.config_hash)})

        SignalRecord(model, dataset, recorder).generate()
        SigAnaRecord(recorder).generate()
        PortAnaRecord(recorder, config=task["port_analysis"]).generate()

        return extract_metrics(recorder)
