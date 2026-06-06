"""Smoke + scoring tests for /persona-accuracy."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    VetoOutcomeEvent,
    append_event,
    utc_now_iso,
)


def _seed_veto_with_outcome(sb_path: Path, regret_pct: float, *,
                            symbol: str = "KO",
                            date: str = "2026-06-01",
                            strategy_id: str = "dow30_lightgbm_committee"):
    append_event(DaySubmittedEvent(
        date=date, universe="dow30", strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        adds=[symbol], committee_active=True,
        committee_verdicts=[CommitteeVerdictSummary(
            symbol=symbol, direction="buy", approved=False,
            n_approve=1, n_veto=3, n_annotate=0,
            summary_rationale="risk", cost_usd=0.001,
        )],
    ), sb_path)
    append_event(VetoOutcomeEvent(
        date=date, universe="dow30", strategy_id=strategy_id,
        timestamp_utc=utc_now_iso(),
        settle_date="2026-06-06",
        symbol=symbol,
        estimated_entry_price=60.0,
        actual_price_at_settle=60.0 * (1 + regret_pct),
        counterfactual_pnl_pct=regret_pct,
        holding_window_days=5,
    ), sb_path)


def test_persona_accuracy_renders_zero_state(client: TestClient, data_root: Path):
    r = client.get("/persona-accuracy")
    assert r.status_code == 200
    assert "No committee vetoes" in r.text


def test_persona_accuracy_shows_over_vetoing_pill_when_avg_regret_positive(
    client: TestClient, data_root: Path,
):
    from dipdiver._paths import ui_scoreboard_path
    sb = ui_scoreboard_path()
    # Three vetoes where the symbol went UP (committee cost money)
    for i, sym in enumerate(("KO", "PG", "JNJ")):
        _seed_veto_with_outcome(
            sb, regret_pct=0.04, symbol=sym,
            date=f"2026-06-0{i + 1}",
        )
    r = client.get("/persona-accuracy")
    assert r.status_code == 200
    body = r.text
    assert "over-vetoing" in body
    assert "risk" in body


def test_persona_accuracy_shows_good_calls_when_avg_regret_negative(
    client: TestClient, data_root: Path,
):
    from dipdiver._paths import ui_scoreboard_path
    sb = ui_scoreboard_path()
    for i, sym in enumerate(("KO", "PG", "JNJ")):
        _seed_veto_with_outcome(
            sb, regret_pct=-0.03, symbol=sym,
            date=f"2026-06-0{i + 1}",
        )
    r = client.get("/persona-accuracy")
    assert "good calls" in r.text


def test_persona_accuracy_counts_overrides(
    client: TestClient, data_root: Path,
):
    """Operator override of a veto to approved is recorded against the persona."""
    from dipdiver._paths import ui_scoreboard_path
    from dipdiver.ui import db
    sb = ui_scoreboard_path()
    _seed_veto_with_outcome(sb, regret_pct=0.05, symbol="KO", date="2026-06-01")
    db.init_db()
    with db.session() as s:
        s.add(db.OverrideDecision(
            date="2026-06-01", universe="dow30", symbol="KO",
            original_decision="vetoed", new_decision="approved",
            reason="conviction was wrong",
            created_utc=datetime.now(timezone.utc), actor="operator",
        ))
    r = client.get("/persona-accuracy")
    assert r.status_code == 200
    body = r.text
    assert "Override wins" in body
    assert "1 / 1" in body
