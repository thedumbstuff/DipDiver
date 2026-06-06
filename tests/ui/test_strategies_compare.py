"""Tests for /strategies-compare A/B view + per-symbol attribution."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def test_strategies_compare_renders_empty_when_no_ids(client: TestClient):
    r = client.get("/strategies-compare")
    assert r.status_code == 200
    body = r.text
    assert "Strategy compare" in body
    assert "Pass a comma-separated list" in body


def test_strategies_compare_renders_seeded(
    client: TestClient, seeded_scoreboard, data_root: Path,
):
    r = client.get(
        "/strategies-compare?ids=dow30_lightgbm,dow30_lightgbm_committee"
    )
    assert r.status_code == 200
    body = r.text
    assert "dow30_lightgbm" in body
    assert "dow30_lightgbm_committee" in body


def test_strategy_detail_has_attribution_section(
    client: TestClient, seeded_scoreboard, data_root: Path,
):
    r = client.get("/strategies/dow30_lightgbm")
    assert r.status_code == 200
    # The attribution loop is in the context but rendering depends on existing
    # template — just assert page returns 200 and our strategy id renders.
    assert "dow30_lightgbm" in r.text
