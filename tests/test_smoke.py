"""Smoke tests — confirm the package and subpackages import cleanly."""

from __future__ import annotations

import importlib


def test_package_imports() -> None:
    import dipdiver

    assert dipdiver.__version__ == "0.0.0"


def test_subpackages_import() -> None:
    for name in (
        "dipdiver.brain",
        "dipdiver.committee",
        "dipdiver.adapters",
        "dipdiver.adapters.lean",
        "dipdiver.harness",
        "dipdiver.brokers",
    ):
        importlib.import_module(name)
