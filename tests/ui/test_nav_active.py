"""Active-nav highlighting tests.

The nav must show which page is current via:
  - `aria-current="page"` on the active <a>
  - a visible `bg-zinc-800` background

Both must be present, and there should be EXACTLY one active link per page.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from dipdiver.ui.helpers import nav_active


# ---------------------------------------------------------------------------
# Pure helper
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("current,link,expected", [
    ("/", "/", True),
    ("/picks", "/", False),  # root only highlights on exact root
    ("/picks", "/picks", True),
    ("/picks?universe=dow30", "/picks", True),  # query string ignored
    ("/strategies/dow30_lightgbm", "/strategies", True),  # detail page highlights parent
    ("/strategies-compare", "/strategies-compare", True),
    # Critical: /strategies-compare must NOT highlight /strategies
    ("/strategies-compare?ids=a,b", "/strategies", False),
    ("/runs/2026-06-04", "/runs", True),
    ("/decisions/2026-06-04/AAPL", "/runs", False),  # decisions is its own tree
    ("/api/available-configs", "/", False),  # API routes don't highlight root
])
def test_nav_active_helper(current, link, expected):
    assert nav_active(current, link) is expected


# ---------------------------------------------------------------------------
# Rendered template
# ---------------------------------------------------------------------------


def _count_aria_current(body: str) -> int:
    return len(re.findall(r'aria-current="page"', body))


def test_root_page_highlights_dashboard(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert _count_aria_current(body) == 1, "exactly one nav link must be marked aria-current"
    # The Dashboard <a> carries aria-current
    m = re.search(r'<a[^>]*href="/"[^>]*aria-current="page"[^>]*>Dashboard</a>', body)
    assert m, "Dashboard link should have aria-current on /"


def test_picks_page_highlights_picks(client: TestClient):
    r = client.get("/picks?universe=dow30")
    assert r.status_code == 200
    body = r.text
    assert _count_aria_current(body) == 1
    assert re.search(r'<a[^>]*href="/picks"[^>]*aria-current="page"[^>]*>Picks</a>', body)


def test_strategies_compare_does_not_highlight_strategies(client: TestClient):
    r = client.get("/strategies-compare?ids=dow30_lightgbm,dow30_lightgbm_committee")
    assert r.status_code == 200
    body = r.text
    # Exactly one active link
    assert _count_aria_current(body) == 1
    # A/B is active, Strategies is NOT
    assert "aria-current" in re.search(
        r'<a[^>]*href="/strategies-compare[^"]*"[^>]*>A/B</a>', body
    ).group(0) or 'aria-current="page"' in body
    # The /strategies link must NOT have aria-current
    strategies_link = re.search(r'<a[^>]*href="/strategies"[^>]*>Strategies</a>', body)
    assert strategies_link, "Strategies link must exist"
    assert 'aria-current' not in strategies_link.group(0)


def test_strategy_detail_highlights_strategies(client: TestClient, seeded_scoreboard):
    r = client.get("/strategies/dow30_lightgbm")
    assert r.status_code == 200
    body = r.text
    assert _count_aria_current(body) == 1
    assert re.search(r'<a[^>]*href="/strategies"[^>]*aria-current="page"', body)


def test_active_link_has_visible_background_class(client: TestClient):
    """The visual treatment (bg-zinc-800) must accompany aria-current."""
    r = client.get("/picks?universe=dow30")
    body = r.text
    # Find the full <a ...>Picks</a> tag with aria-current — class attribute
    # may appear before OR after href / aria-current, and Jinja whitespace
    # spans multiple lines.
    m = re.search(
        r'<a\b[^>]*aria-current="page"[^>]*>Picks</a>',
        body,
        re.DOTALL,
    )
    assert m, "expected an active Picks link with aria-current"
    tag = m.group(0)
    assert 'href="/picks"' in tag
    assert "bg-zinc-800" in tag, f"active link missing visible background, tag was: {tag}"
