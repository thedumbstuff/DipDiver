"""Regression: jobs that import from `scripts.*` must still work when CWD
is not the repo root (the `dipdiver-ui` console_scripts entry case).
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _drop_scripts_from_path(monkeypatch):
    """Remove any path that resolves to the repo's scripts/ dir + any
    cached scripts modules, so the import has to re-resolve via the job's
    sys.path injection.
    """
    from dipdiver._paths import repo_root
    repo = str(repo_root())
    new_path = [p for p in sys.path if p != repo]
    monkeypatch.setattr(sys, "path", new_path)
    for k in list(sys.modules):
        if k == "scripts" or k.startswith("scripts."):
            monkeypatch.delitem(sys.modules, k, raising=False)


def _assert_scripts_import_works(job_mod, script_name: str):
    """Call the job's path-injection helper, then import the script directly.

    We don't call run() — that would execute the real Qlib/LLM pipeline and
    take minutes. The point of this regression is to prove the import path
    resolves, which is the actual bug the fix addresses.
    """
    job_mod._ensure_repo_on_path()
    mod = importlib.import_module(f"scripts.{script_name}")
    assert hasattr(mod, "main"), f"scripts.{script_name}.main missing"


def test_signal_refresh_imports_scripts_even_without_repo_on_path(
    monkeypatch, data_root: Path,
):
    _drop_scripts_from_path(monkeypatch)
    import dipdiver.ui.jobs.signal_refresh as job_mod
    importlib.reload(job_mod)
    _assert_scripts_import_works(job_mod, "m3_export_signals")


def test_m2_lite_imports_scripts_even_without_repo_on_path(
    monkeypatch, data_root: Path,
):
    _drop_scripts_from_path(monkeypatch)
    import dipdiver.ui.jobs.m2_lite as job_mod
    importlib.reload(job_mod)
    _assert_scripts_import_works(job_mod, "m2_lite_run")
