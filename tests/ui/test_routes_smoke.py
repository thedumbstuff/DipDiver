"""All GET routes return 200 from cold-start, and again from seeded state.

If a regression breaks a page (missing template, bad context var, import
error), this catches it immediately.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


GET_ROUTES_NO_PARAMS = [
    "/",
    "/strategies",
    "/runs",
    "/positions",
    "/scoreboard",
    "/scoreboard.md",
    "/triggers",
    "/config",
    "/schedule",
    "/health",
    "/logs",
]


# ---------------------------------------------------------------------------
# Cold start — empty data root
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", GET_ROUTES_NO_PARAMS)
def test_get_route_returns_200_cold_start(client: TestClient, path: str):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}: {r.text[:300]}"


def test_strategies_lists_no_strategies_when_empty(client: TestClient):
    r = client.get("/strategies")
    body = r.text
    assert "No strategies have run yet" in body or "Strategies" in body


def test_runs_list_handles_empty_scoreboard(client: TestClient):
    r = client.get("/runs")
    assert r.status_code == 200
    body = r.text
    # Empty state copy
    assert "Runs" in body


# ---------------------------------------------------------------------------
# Seeded state — scoreboard has rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", GET_ROUTES_NO_PARAMS)
def test_get_route_returns_200_seeded(client: TestClient, seeded_scoreboard, path: str):
    r = client.get(path)
    assert r.status_code == 200, f"{path} → {r.status_code}"


def test_scoreboard_page_renders_seeded_rows(client: TestClient, seeded_scoreboard):
    r = client.get("/scoreboard")
    body = r.text
    assert "2026-06-03" in body
    assert "2026-06-04" in body
    assert "dow30_lightgbm" in body
    assert "dow30_lightgbm_committee" in body


def test_strategies_page_lists_both_strategies(client: TestClient, seeded_scoreboard):
    r = client.get("/strategies")
    body = r.text
    assert "dow30_lightgbm" in body
    assert "dow30_lightgbm_committee" in body


def test_dashboard_shows_recent_rows(client: TestClient, seeded_scoreboard):
    r = client.get("/")
    body = r.text
    # Both strategies appear on the latest-7 table
    assert "dow30_lightgbm" in body


def test_scoreboard_md_endpoint_returns_markdown(client: TestClient, seeded_scoreboard):
    r = client.get("/scoreboard.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "# DipDiver Scoreboard" in r.text


# ---------------------------------------------------------------------------
# Drill-down routes (need fixture state)
# ---------------------------------------------------------------------------


def test_run_detail_404_when_missing(client: TestClient):
    r = client.get("/runs/2099-01-01?universe=dow30")
    assert r.status_code == 404


def test_run_detail_200_when_record_exists(
    client: TestClient, seeded_run_record, patch_repo_root_to_data_root,
):
    r = client.get("/runs/2026-06-04?universe=dow30")
    assert r.status_code == 200
    body = r.text
    assert "CVX" in body
    assert "Committee decisions" in body
    assert "test-order-id" in body


def test_decision_detail_404_when_symbol_missing(
    client: TestClient, seeded_run_record, patch_repo_root_to_data_root,
):
    r = client.get("/decisions/2026-06-04/UNKNOWN?universe=dow30")
    assert r.status_code == 404


def test_decision_detail_renders_per_persona(
    client: TestClient, seeded_run_record, patch_repo_root_to_data_root,
):
    r = client.get("/decisions/2026-06-04/CVX?universe=dow30")
    assert r.status_code == 200
    body = r.text
    for persona in ("fundamental", "technical", "risk", "value"):
        assert persona in body
    assert "APPROVED" in body  # outcome pill
    assert "solid fundamentals" in body  # rationale leaked through


def test_strategy_detail_works_with_seeded(client: TestClient, seeded_scoreboard):
    r = client.get("/strategies/dow30_lightgbm_committee")
    assert r.status_code == 200
    body = r.text
    assert "dow30_lightgbm_committee" in body


def test_strategy_detail_unknown_strategy_renders_empty(client: TestClient):
    """Unknown strategy_id should render the empty-state, not 500."""
    r = client.get("/strategies/does_not_exist")
    assert r.status_code == 200
    body = r.text
    # The page renders with no per-day rows
    assert "No runs for this strategy" in body


# ---------------------------------------------------------------------------
# Responsive markers — every page that should be mobile-friendly is
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", GET_ROUTES_NO_PARAMS)
def test_pages_have_viewport_meta(client: TestClient, seeded_scoreboard, path: str):
    r = client.get(path)
    # scoreboard.md is plain text; skip it
    if path == "/scoreboard.md":
        return
    assert 'name="viewport"' in r.text, f"{path} missing viewport meta"


def test_tables_are_wrapped_for_horizontal_scroll(
    client: TestClient, seeded_scoreboard,
):
    """Every table on every page must sit inside an overflow-x-auto wrapper."""
    import re
    for path in ("/", "/strategies", "/scoreboard", "/health"):
        r = client.get(path)
        body = r.text
        for m in re.finditer(r"<table\b", body):
            # Look back 300 chars for the wrapper.
            start = max(0, m.start() - 300)
            assert "overflow-x-auto" in body[start:m.start()], (
                f"{path}: <table> at offset {m.start()} not inside overflow-x-auto"
            )


def test_nav_supports_flex_wrap(client: TestClient):
    """The nav must flex-wrap so it doesn't overflow on phones."""
    r = client.get("/")
    body = r.text
    # base.html nav has flex-wrap on the header row
    assert "flex-wrap" in body
