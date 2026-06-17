"""Shared fixtures for UI tests.

Per-test isolation strategy:
- Every test gets its own data root via DIPDIVER_UI_DATA_ROOT pointing to a tmpdir.
- Module-level caches in scoreboard/db/scheduler/settings are reset so the
  next test sees the fresh root.
- The FastAPI app factory is invoked fresh per test (no shared state across tests).

External services (Alpaca, Telegram, LLM providers) are NOT touched by these
fixtures. The few tests that need them use explicit mock fixtures or are
marked @pytest.mark.integration.
"""

from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated data root per test. Sets DIPDIVER_UI_DATA_ROOT and resets module caches."""
    monkeypatch.setenv("DIPDIVER_UI_DATA_ROOT", str(tmp_path))

    # Reset module-level caches that read paths or env at import time.
    import dipdiver.ui.settings as settings_mod
    settings_mod._ui_config_cache = None  # type: ignore[attr-defined]
    settings_mod.env_settings.cache_clear()

    import dipdiver.ui.db as db_mod
    db_mod._engine = None  # type: ignore[attr-defined]
    db_mod._SessionLocal = None  # type: ignore[attr-defined]

    # Tear down any APScheduler from a previous test
    import dipdiver.ui.jobs.scheduler as sched_mod
    if sched_mod._scheduler is not None and sched_mod._scheduler.running:
        sched_mod._scheduler.shutdown(wait=False)
    sched_mod._scheduler = None  # type: ignore[attr-defined]

    return tmp_path


@pytest.fixture()
def client(data_root: Path):
    """FastAPI TestClient bound to an isolated data root.

    Triggers app creation AFTER data_root is set, so lifespan handlers
    (DB init + scheduler boot) hit the right files.
    """
    from fastapi.testclient import TestClient

    # Force a fresh app module so create_app() reads current env.
    import dipdiver.ui.app as app_mod
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture()
def mock_alpaca(monkeypatch: pytest.MonkeyPatch):
    """Replace AlpacaPaperClient with a MagicMock for kill-switch / positions tests."""
    fake_account = MagicMock(
        equity=99400.04, cash=0.0, buying_power=198800.07,
        status="ACTIVE",
    )
    fake_position = MagicMock(
        symbol="AAPL", qty=10.0, side="long",
        market_value=2500.0, avg_entry_price=240.0,
        unrealized_pl=100.0, unrealized_plpc=0.04,
    )
    fake_position.symbol = "AAPL"  # MagicMock auto-stub doesn't preserve string attrs

    fake_client = MagicMock()
    fake_client.get_account.return_value = fake_account
    fake_client.market_is_open.return_value = True
    fake_client.get_positions.return_value = [fake_position]
    # Underlying alpaca-py client used by /health kill-switch
    fake_client.client = MagicMock()
    fake_client.client.cancel_orders = MagicMock()
    fake_client.client.close_all_positions = MagicMock()

    # Patch the import target inside the routes that import it.
    import dipdiver.adapters.alpaca.client as alpaca_mod
    monkeypatch.setattr(
        alpaca_mod, "AlpacaPaperClient", lambda *a, **k: fake_client
    )
    return fake_client


# ---------------------------------------------------------------------------
# Sample data fixtures
# ---------------------------------------------------------------------------


def _sample_day_submitted_event(
    *,
    date: str = "2026-06-04",
    universe: str = "dow30",
    strategy_id: str = "dow30_lightgbm_committee",
    committee_active: bool = True,
    n_buys: int = 3,
    n_vetoed: int = 0,
) -> dict[str, Any]:
    """A realistic day_submitted event for seeding the scoreboard."""
    decisions = []
    orders = []
    target = []
    for i in range(n_buys):
        sym = f"SYM{i}"
        target.append(sym)
        approved = (i >= n_vetoed)
        if committee_active:
            decisions.append({
                "symbol": sym,
                "direction": "buy",
                "approved": approved,
                "n_approve": 3 if approved else 1,
                "n_veto": 0 if approved else 3,
                "n_annotate": 1 if approved else 0,
                "summary_rationale": "test rationale",
                "cost_usd": 0.001,
            })
        if approved:
            orders.append({
                "symbol": sym, "side": "buy", "notional_usd": 9940.0,
                "order_id": f"oid-{date}-{sym}",
                "submitted_at_utc": f"{date}T01:45:21Z",
                "status": "ACCEPTED",
            })
    return {
        "event_type": "day_submitted",
        "date": date,
        "universe": universe,
        "strategy_id": strategy_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config_hash": "e8f78c493ccf115a",
        "config_name": "dow30_lightgbm.yaml",
        "signal_date_used": date,
        "target_holdings": target,
        "current_holdings_pre": [],
        "adds": [d["symbol"] for d in decisions] if committee_active else target,
        "removes": [],
        "committee_active": committee_active,
        "committee_verdicts": decisions,
        "orders_submitted": orders,
        "account_equity_pre": 99400.04,
        "account_buying_power_pre": 198800.07,
        "market_open_at_submit": False,
        "dry_run": False,
        "source_run_record": f"logs/m3_live/{universe}/{date}.json",
    }


@pytest.fixture()
def seeded_scoreboard(data_root: Path) -> Path:
    """Write 2 day_submitted events into the test scoreboard.jsonl."""
    from dipdiver._paths import ui_scoreboard_path

    path = ui_scoreboard_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        _sample_day_submitted_event(
            date="2026-06-03", strategy_id="dow30_lightgbm",
            committee_active=False, n_buys=10, n_vetoed=0,
        ),
        _sample_day_submitted_event(
            date="2026-06-04", strategy_id="dow30_lightgbm_committee",
            committee_active=True, n_buys=3, n_vetoed=1,
        ),
    ]
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path


@pytest.fixture()
def seeded_run_record(data_root: Path) -> Path:
    """Write a sample m3_live run record under logs/m3_live/dow30/ so /runs/<date>
    drill-down works.
    """
    record_path = data_root / "logs" / "m3_live" / "dow30" / "2026-06-04.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": "2026-06-04T01:45:21+00:00",
        "dry_run": False,
        "signal_date_used": "2025-12-31",
        "config_name": "dow30_lightgbm.yaml",
        "config_hash": "e8f78c493ccf115a",
        "universe": "dow30",
        "market_open": False,
        "account": {"cash": 0.0, "equity": 99400.04, "buying_power": 198800.07, "status": "ACTIVE"},
        "current_holdings_pre": ["AAPL"],
        "target_post": ["AAPL", "CVX"],
        "adds": ["CVX"],
        "removes": [],
        "orders": [{
            "symbol": "CVX", "side": "buy", "notional": 9940.0,
            "id": "test-order-id", "status": "ACCEPTED",
            "submitted_at": "2026-06-04 01:45:21+00:00",
        }],
        "committee_decisions": [{
            "symbol": "CVX", "direction": "buy", "approved": True,
            "n_approve": 3, "n_veto": 0, "n_annotate": 1,
            "summary": "test summary",
            "cost_usd": 0.001,
            "verdicts": [
                {"persona": "fundamental", "decision": "approve",
                 "confidence": 0.85, "rationale": "solid fundamentals"},
                {"persona": "technical", "decision": "annotate",
                 "confidence": 0.7, "rationale": "near 52w high"},
                {"persona": "risk", "decision": "approve",
                 "confidence": 0.9, "rationale": "diversifies"},
                {"persona": "value", "decision": "approve",
                 "confidence": 0.85, "rationale": "reasonable yield"},
            ],
        }],
    }
    # /runs drill-down reads from repo_root/logs/m3_live/, NOT from data_root.
    # Patch the reader to look under data_root instead.
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    # Also seed at the repo's real logs/m3_live/ path since helpers.load_run_record
    # uses repo_root() — but we don't want to pollute. Instead, patch repo_root.
    return record_path


@pytest.fixture()
def patch_repo_root_to_data_root(data_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect repo_root() to the per-test data_root tmpdir.

    helpers.load_run_record now reads run records via ui_logs_dir() (=
    <DIPDIVER_UI_DATA_ROOT>/logs), which the `data_root` fixture already points
    at this tmpdir — so the seeded record is found without any patching. We keep
    redirecting repo_root() (with raising=False, since callers may not have it
    imported) for any other helper still resolving paths off the repo root.
    """
    import dipdiver._paths as paths_mod
    monkeypatch.setattr(paths_mod, "repo_root", lambda: data_root)
    return data_root
