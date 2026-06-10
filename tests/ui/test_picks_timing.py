"""Pick-of-the-day + entry/exit timing on /picks.

The vision: the user chooses a universe (dow30, nifty50, world_indices, ...)
and the board answers "what's the good pick of the day, when do I enter,
when do I exit". These tests pin that behavior.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from dipdiver.harness.picks import EnrichedPick, attach_timing, pick_of_the_day


def _pick(rank, symbol, *, score=0.1, decision=None, on_watchlist=False):
    return EnrichedPick(
        rank=rank, symbol=symbol, score=score, signal_date="2026-06-09",
        universe="dow30", decision=decision, on_watchlist=on_watchlist,
    )


def _write_signal_csv(repo_root: Path, stem: str, rows: list[tuple[str, str, float]]):
    p = repo_root / "data" / "signals" / f"{stem}.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["date,symbol,score"]
    for d, s, sc in rows:
        lines.append(f"{d},{s},{sc}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _patch_repo_root(data_root: Path, monkeypatch):
    import dipdiver._paths as paths_mod
    monkeypatch.setattr(paths_mod, "repo_root", lambda: data_root)
    import dipdiver.harness.picks as picks_mod
    monkeypatch.setattr(picks_mod, "repo_root", lambda: data_root)


# ---------------------------------------------------------------------------
# pick_of_the_day selection
# ---------------------------------------------------------------------------


def test_prefers_best_ranked_approved_over_higher_ranked_undecided():
    picks = [
        _pick(1, "AAPL", decision=None),
        _pick(2, "MSFT", decision="approved"),
        _pick(3, "AMZN", decision="approved"),
    ]
    top = pick_of_the_day(picks)
    assert top is not None and top.symbol == "MSFT"


def test_vetoed_pick_never_wins_even_at_rank_one():
    picks = [
        _pick(1, "AAPL", decision="vetoed"),
        _pick(2, "MSFT", decision=None),
    ]
    top = pick_of_the_day(picks)
    assert top is not None and top.symbol == "MSFT"


def test_all_vetoed_returns_none():
    picks = [
        _pick(1, "AAPL", decision="vetoed"),
        _pick(2, "MSFT", decision="vetoed"),
    ]
    assert pick_of_the_day(picks) is None


def test_watchlist_filler_does_not_qualify():
    # merge_watchlist appends score-0 fillers — they're not real signals.
    picks = [
        _pick(1, "AAPL", decision="vetoed"),
        _pick(2, "GE", score=0.0, on_watchlist=True),
    ]
    assert pick_of_the_day(picks) is None


def test_empty_list_returns_none():
    assert pick_of_the_day([]) is None


# ---------------------------------------------------------------------------
# attach_timing
# ---------------------------------------------------------------------------


def test_attach_timing_populates_every_pick():
    picks = [_pick(1, "AAPL"), _pick(2, "MSFT")]
    out = attach_timing(picks, universe="dow30")
    for p in out:
        assert p.timing is not None
        assert p.timing.entry_local == "09:45"
        assert p.timing.exit_local == "15:45"


def test_attach_timing_world_indices_per_symbol():
    picks = [
        EnrichedPick(rank=1, symbol="^N225", score=0.2,
                     signal_date="2026-06-09", universe="world_indices"),
        EnrichedPick(rank=2, symbol="^FTSE", score=0.1,
                     signal_date="2026-06-09", universe="world_indices"),
    ]
    out = attach_timing(picks, universe="world_indices")
    assert out[0].timing.tz_name == "Asia/Tokyo"
    assert out[1].timing.tz_name == "Europe/London"


# ---------------------------------------------------------------------------
# /picks rendering
# ---------------------------------------------------------------------------


def test_picks_page_shows_pick_of_the_day_with_entry_exit(
    client: TestClient, data_root: Path, monkeypatch,
):
    _patch_repo_root(data_root, monkeypatch)
    _write_signal_csv(data_root, "dow30_lightgbm", [
        ("2026-06-09", "AAPL", 0.12),
        ("2026-06-09", "MSFT", 0.08),
    ])
    r = client.get("/picks?universe=dow30")
    assert r.status_code == 200
    body = r.text
    assert "Good pick of the day" in body
    assert "Suggested entry" in body
    assert "Suggested exit" in body
    assert "09:45" in body  # NYSE entry: open 09:30 + 15 min
    assert "15:45" in body  # NYSE exit: close 16:00 - 15 min


def test_unconfigured_universe_falls_back_to_default_signal_csv(
    client: TestClient, data_root: Path, monkeypatch,
):
    """nifty50 has no strategy in the default ui_config — the board must
    still render picks from data/signals/nifty50_lightgbm.csv with NSE
    (IST) entry/exit times, not dead-end on the zero state.
    """
    _patch_repo_root(data_root, monkeypatch)
    _write_signal_csv(data_root, "nifty50_lightgbm", [
        ("2026-06-09", "RELIANCE.NS", 0.15),
        ("2026-06-09", "TCS.NS", 0.10),
    ])
    r = client.get("/picks?universe=nifty50")
    assert r.status_code == 200
    body = r.text
    assert "No signal CSV found" not in body
    assert "RELIANCE.NS" in body
    assert "Good pick of the day" in body
    assert "09:30" in body  # NSE entry: open 09:15 + 15 min
    assert "15:15" in body  # NSE exit: close 15:30 - 15 min


def test_world_indices_fallback_renders_home_exchange_times(
    client: TestClient, data_root: Path, monkeypatch,
):
    _patch_repo_root(data_root, monkeypatch)
    _write_signal_csv(data_root, "world_indices_lightgbm", [
        ("2026-06-09", "^N225", 0.2),
    ])
    r = client.get("/picks?universe=world_indices")
    assert r.status_code == 200
    body = r.text
    assert "^N225" in body
    assert "Asia/Tokyo" in body
    assert "09:15" in body  # TSE entry: open 09:00 + 15 min


def test_no_signal_universe_still_shows_zero_state(
    client: TestClient, data_root: Path, monkeypatch,
):
    """Fallback must not invent picks when even the default CSV is absent."""
    _patch_repo_root(data_root, monkeypatch)
    r = client.get("/picks?universe=nifty50")
    assert r.status_code == 200
    assert "No signal CSV found" in r.text
