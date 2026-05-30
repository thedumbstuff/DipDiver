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
    )


def _qlib_region(region: str) -> Any:
    """Map our region code to Qlib's REG_* constant."""
    from qlib.config import REG_CN, REG_US

    # NIFTY and crypto reuse the US calendar+region defaults; M2 may revisit.
    return {"us": REG_US, "in": REG_US, "crypto": REG_US, "cn": REG_CN}.get(region, REG_US)


def _run_qlib_workflow(config: BaselineConfig) -> dict[str, Any]:
    """Invoke Qlib's workflow on the config and return headline metrics."""
    import qlib
    from qlib.utils import init_instance_by_config
    from qlib.workflow import R
    from qlib.workflow.record_temp import PortAnaRecord, SigAnaRecord, SignalRecord

    from dipdiver._paths import resolve_provider_uri
    from dipdiver.brain.baselines._qlib.metrics import extract_metrics
    from dipdiver.brain.baselines._qlib.task import build_task

    qlib.init(
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
