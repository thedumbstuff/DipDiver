"""Forward-looking suggestion board — /picks.

For a given universe and strategy, show the top-N candidates for the next
trading day with conviction, weight %, rationale, and feedback markers.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dipdiver.harness.picks import (
    apply_feedback_penalty,
    attach_timing,
    enrich_with_committee,
    load_next_signal_forecast,
    merge_watchlist,
    pick_of_the_day,
    signal_csv_path,
    signal_file_mtime_hours,
    signal_freshness_hours,
    size_by_risk_band,
)
from dipdiver.harness.scoreboard import read_events
from dipdiver.ui.helpers import template_ctx
from dipdiver.ui.settings import ui_config


router = APIRouter()


def _resolve_strategy_for(universe: str, prefer_committee: bool = True):
    """Pick a strategy in `ui_config.yaml` that matches the requested universe.

    Prefers `*_committee` variant when present — that's where conviction lives.
    Returns (strategy_id, config_stem) or (None, None) if no match.
    """
    cfg = ui_config()
    candidates = [
        s for s in cfg.strategies
        if s.enabled and s.strategy_id.startswith(universe)
    ]
    if not candidates:
        # No configured strategy for this universe (research universes like
        # nifty50 / world_indices usually aren't in ui_config.yaml). Fall back
        # to the universe's default signal CSV so the board still renders.
        fallback_stem = f"{universe}_lightgbm"
        if signal_csv_path(fallback_stem).exists():
            return None, fallback_stem
        return None, None
    if prefer_committee:
        for s in candidates:
            if s.with_committee:
                return s.strategy_id, s.m1_config.replace(".yaml", "")
    s = candidates[0]
    return s.strategy_id, s.m1_config.replace(".yaml", "")


@router.get("/picks", response_class=HTMLResponse)
async def picks_page(
    request: Request,
    universe: str = "dow30",
    risk: str | None = None,
    strategy_id: str | None = None,
    top_n: int = 10,
):
    from dipdiver.ui.app import templates

    cfg = ui_config()
    band = (risk or cfg.risk_band or "balanced").strip().lower()
    if band not in ("aggressive", "balanced", "conservative"):
        band = "balanced"

    if strategy_id is None:
        sid, stem = _resolve_strategy_for(universe)
    else:
        sid = strategy_id
        # Try to find this strategy's config stem
        match = next(
            (s for s in cfg.strategies if s.strategy_id == strategy_id), None
        )
        stem = match.m1_config.replace(".yaml", "") if match else None

    picks_raw: list = []
    freshness: float | None = None
    file_mtime_hours: float | None = None
    csv_p = None
    if stem:
        csv_p = signal_csv_path(stem)
        picks_raw = load_next_signal_forecast(stem, top_n=top_n)
        freshness = signal_freshness_hours(csv_p)
        file_mtime_hours = signal_file_mtime_hours(csv_p)

    events = read_events()
    enriched = enrich_with_committee(
        picks_raw, universe=universe, strategy_id=sid, events=events,
    )
    enriched = apply_feedback_penalty(
        enriched,
        penalty=getattr(
            next((s for s in cfg.strategies if s.strategy_id == sid), None),
            "feedback_rank_penalty",
            0.85,
        ) if sid else 1.0,
        lookback_days=cfg.feedback_lookback_days,
        universe=universe,
    )
    enriched = size_by_risk_band(enriched, band)
    enriched = merge_watchlist(enriched, universe=universe, top_n=top_n)
    enriched = attach_timing(enriched, universe=universe)

    # Headline suggestion — best approved (or undecided) pick. None when the
    # committee vetoed everything; the template explains rather than pushing
    # a vetoed name.
    top_pick = pick_of_the_day(enriched)

    # Two distinct staleness concepts (see signal_freshness_hours vs
    # signal_file_mtime_hours docstrings):
    #
    #   model_window_stale: the latest signal DATE is more than 7 days old.
    #     This is gated by the M1 model's test_end and ONLY advances on
    #     m1_retrain. Running signal_refresh alone won't fix it.
    #
    #   file_refresh_stale: the CSV file mtime is more than 48 hours old.
    #     Fixed by running signal_refresh.
    model_window_stale = freshness is not None and freshness > (7 * 24)
    file_refresh_stale = file_mtime_hours is not None and file_mtime_hours > 48.0

    # Research-only banner for non-live-tradable universes (M13)
    research_only = False
    try:
        from dipdiver.brain.baselines.universes import get_universe
        u = get_universe(universe)
        research_only = not getattr(u, "live_executable", True)
    except Exception:  # noqa: BLE001
        pass

    ctx = template_ctx(
        request,
        universe=universe,
        risk_band=band,
        strategy_id=sid,
        picks=enriched,
        top_pick=top_pick,
        freshness_hours=freshness,
        file_mtime_hours=file_mtime_hours,
        model_window_stale=model_window_stale,
        file_refresh_stale=file_refresh_stale,
        signal_path=str(csv_p) if csv_p else None,
        no_signal=(not picks_raw),
        risk_bands=("aggressive", "balanced", "conservative"),
        research_only=research_only,
    )
    return templates.TemplateResponse(request, "picks.html", ctx)


# ---------------------------------------------------------------------------
# Watchlist write endpoint (Stage 5 — referenced from /picks template)
# ---------------------------------------------------------------------------


from datetime import datetime, timezone

from fastapi import Form
from fastapi.responses import RedirectResponse

from dipdiver.ui import db


@router.post("/watchlist/add")
async def watchlist_add(
    symbol: str = Form(...),
    universe: str = Form("dow30"),
    notes: str = Form(""),
):
    sym = (symbol or "").strip().upper()
    if not sym:
        return RedirectResponse(f"/picks?universe={universe}", status_code=303)
    with db.session() as s:
        s.add(db.WatchlistEntry(
            symbol=sym, universe=universe, notes=notes.strip(),
            added_utc=datetime.now(timezone.utc), actor="operator",
        ))
    return RedirectResponse(f"/picks?universe={universe}", status_code=303)


@router.post("/watchlist/remove")
async def watchlist_remove(
    symbol: str = Form(...),
    universe: str = Form("dow30"),
):
    sym = (symbol or "").strip().upper()
    with db.session() as s:
        rows = s.query(db.WatchlistEntry).filter_by(symbol=sym, universe=universe).all()
        for r in rows:
            s.delete(r)
    return RedirectResponse(f"/picks?universe={universe}", status_code=303)


@router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, universe: str = "dow30"):
    from dipdiver.ui.app import templates
    with db.session() as s:
        rows = (
            s.query(db.WatchlistEntry)
            .filter_by(universe=universe)
            .order_by(db.WatchlistEntry.added_utc.desc())
            .all()
        )
        view = [
            {
                "symbol": r.symbol,
                "notes": r.notes,
                "added": r.added_utc.strftime("%Y-%m-%d %H:%M UTC"),
            }
            for r in rows
        ]
    ctx = template_ctx(request, universe=universe, entries=view)
    return templates.TemplateResponse(request, "watchlist.html", ctx)
