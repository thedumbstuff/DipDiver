"""Regression: safe_qlib_init must tolerate re-init across universes.

qlib is one-init-per-process by design; the ops UI re-inits per universe (and
twice per onboarding: train + signal export). A finished experiment leaves
exp_manager.active_experiment set, and a raw qlib.init then raises
RecorderInitializationError. safe_qlib_init clears the stale reference first.
"""

from __future__ import annotations

import tempfile

import pytest


def _exp_manager_kwargs(tmpdir: str) -> dict:
    uri = "file:" + tmpdir.replace("\\", "/") + "/mlruns"
    return {
        "class": "MLflowExpManager",
        "module_path": "qlib.workflow.expm",
        "kwargs": {"uri": uri, "default_exp_name": "test"},
    }


def test_safe_qlib_init_survives_reinit_with_active_experiment(monkeypatch):
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    qlib = pytest.importorskip("qlib")
    from qlib.utils.exceptions import RecorderInitializationError
    from qlib.workflow import R

    from dipdiver.brain.baselines._qlib.init import safe_qlib_init

    tmp = tempfile.mkdtemp()
    p1, p2 = tempfile.mkdtemp(), tempfile.mkdtemp()

    safe_qlib_init(provider_uri=p1, region="us", exp_manager=_exp_manager_kwargs(tmp))

    # Mimic the leftover-active-experiment state a prior training run leaves.
    R.exp_manager.active_experiment = R.get_exp(experiment_name="leftover", start=True)

    # Raw init reproduces the production failure.
    with pytest.raises(RecorderInitializationError):
        qlib.init(provider_uri=p2, region="us", exp_manager=_exp_manager_kwargs(tmp))

    # safe_qlib_init clears the stale reference and re-inits cleanly.
    R.exp_manager.active_experiment = R.get_exp(experiment_name="leftover2", start=True)
    safe_qlib_init(provider_uri=p2, region="us", exp_manager=_exp_manager_kwargs(tmp))
    assert R.exp_manager.active_experiment is None
