"""Forward-looking suggestion board data layer.

`data/signals/<config_stem>.csv` already carries next-day predictions per
symbol per date (one row per (date, symbol, score)). This module surfaces
the latest date's picks, attaches committee context when it exists, applies
operator-feedback rank penalties, and computes risk-band-driven weights.

Used by `dipdiver/ui/routes/picks.py` to render `/picks`.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from dipdiver._paths import repo_root
from dipdiver.harness.render import FusedDayRow
from dipdiver.harness.scoreboard import (
    CommitteeVerdictSummary,
    DaySubmittedEvent,
    ScoreboardEvent,
    read_events,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Pick:
    """One forward-looking recommendation, before sizing/penalty."""

    rank: int
    symbol: str
    score: float
    signal_date: str  # YYYY-MM-DD — the signal we're acting on


@dataclass
class EnrichedPick:
    """A Pick augmented with committee context, feedback, and sizing."""

    rank: int
    symbol: str
    score: float
    signal_date: str
    universe: str
    strategy_id: str | None = None
    weight_pct: float = 0.0  # 0..100
    conviction: float | None = None  # mean(persona.confidence) for approves
    decision: str | None = None  # approved | vetoed | (None = no committee)
    summary_rationale: str | None = None
    feedback_penalty_applied: bool = False
    on_watchlist: bool = False


# ---------------------------------------------------------------------------
# Signal CSV loader
# ---------------------------------------------------------------------------


_RISK_BAND_WEIGHTS = {
    "aggressive": 5.0,
    "balanced": 3.0,
    "conservative": 1.0,
}


def signal_csv_path(config_stem: str) -> Path:
    """Default location of the signal CSV for a given config stem.

    e.g. config_stem='dow30_lightgbm' -> data/signals/dow30_lightgbm.csv
    """
    return repo_root() / "data" / "signals" / f"{config_stem}.csv"


def latest_signal_date(csv_path: Path) -> str | None:
    """Return the max date present in the signal CSV (no rows → None)."""
    if not csv_path.exists():
        return None
    latest: str | None = None
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            d = (row.get("date") or "").strip()
            if not d:
                continue
            if latest is None or d > latest:
                latest = d
    return latest


def load_next_signal_forecast(
    config_stem: str,
    *,
    csv_path: Path | None = None,
    top_n: int = 10,
) -> list[Pick]:
    """Read the latest day's predictions from `data/signals/{config_stem}.csv`,
    sorted by score descending. Returns up to `top_n` picks.

    Returns an empty list when the file is missing or empty (zero-state safe).
    """
    path = csv_path or signal_csv_path(config_stem)
    if not path.exists():
        return []
    latest = latest_signal_date(path)
    if latest is None:
        return []
    rows: list[tuple[str, float]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if (row.get("date") or "").strip() != latest:
                continue
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                continue
            try:
                score = float(row.get("score") or "nan")
            except (TypeError, ValueError):
                continue
            if score != score:  # NaN
                continue
            rows.append((sym, score))
    rows.sort(key=lambda x: x[1], reverse=True)
    rows = rows[:top_n]
    return [
        Pick(rank=i + 1, symbol=sym, score=score, signal_date=latest)
        for i, (sym, score) in enumerate(rows)
    ]


def signal_freshness_hours(csv_path: Path) -> float | None:
    """How many hours since the latest signal date (UTC)?

    NOTE: this is "how old is the LATEST PREDICTION" — gated by the M1 model's
    test_end. Even after rerunning `signal_refresh`, this stays the same until
    the M1 model is retrained with a rolled window (`m1_retrain`).

    See also `signal_file_mtime_hours()` which tells you when the CSV was
    last regenerated — that DOES update on `signal_refresh`.

    Returns None if the file is missing or has no date column.
    """
    latest = latest_signal_date(csv_path)
    if latest is None:
        return None
    try:
        latest_dt = datetime.strptime(latest, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    delta = datetime.now(timezone.utc) - latest_dt
    return delta.total_seconds() / 3600.0


def signal_file_mtime_hours(csv_path: Path) -> float | None:
    """How many hours since the signal CSV was last regenerated.

    This updates whenever `signal_refresh` runs — distinct from the
    `signal_freshness_hours` (which is gated by the model's test_end and
    only advances on `m1_retrain`).

    Returns None if the file doesn't exist.
    """
    if not csv_path.exists():
        return None
    try:
        mtime = csv_path.stat().st_mtime
    except OSError:
        return None
    age = datetime.now(timezone.utc).timestamp() - mtime
    return age / 3600.0


# ---------------------------------------------------------------------------
# Enrichment from committee + feedback
# ---------------------------------------------------------------------------


def _latest_day_for_strategy(
    events: Iterable[ScoreboardEvent], strategy_id: str
) -> DaySubmittedEvent | None:
    """Find the most recent DaySubmittedEvent for a strategy."""
    latest: DaySubmittedEvent | None = None
    for e in events:
        if e.event_type != "day_submitted":
            continue
        if e.strategy_id != strategy_id:
            continue
        if latest is None or e.date > latest.date:
            latest = e  # type: ignore[assignment]
    return latest


def enrich_with_committee(
    picks: list[Pick],
    *,
    universe: str,
    strategy_id: str | None,
    events: list[ScoreboardEvent] | None = None,
) -> list[EnrichedPick]:
    """Attach conviction + decision + rationale from the most recent
    DaySubmittedEvent for this strategy. Falls back gracefully when no
    committee verdict matches a picked symbol.
    """
    if events is None:
        events = read_events()
    verdicts_by_symbol: dict[str, CommitteeVerdictSummary] = {}
    if strategy_id:
        recent = _latest_day_for_strategy(events, strategy_id)
        if recent:
            for v in recent.committee_verdicts:
                verdicts_by_symbol[v.symbol.upper()] = v

    out: list[EnrichedPick] = []
    for p in picks:
        v = verdicts_by_symbol.get(p.symbol)
        decision = None
        conviction = None
        rationale = None
        if v is not None:
            decision = "approved" if v.approved else "vetoed"
            # Conviction = (n_approve - n_veto) / total — simple proxy until
            # we surface per-persona confidence on the summary record.
            denom = v.n_approve + v.n_veto + v.n_annotate
            if denom:
                conviction = max(0.0, min(1.0, (v.n_approve - v.n_veto) / denom + 0.5))
            rationale = v.summary_rationale or None
        out.append(EnrichedPick(
            rank=p.rank, symbol=p.symbol, score=p.score,
            signal_date=p.signal_date, universe=universe, strategy_id=strategy_id,
            conviction=conviction, decision=decision, summary_rationale=rationale,
        ))
    return out


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


def weight_pct_for_risk_band(band: str) -> float:
    """% of equity per pick. Anything not in the table defaults to 'balanced'."""
    return _RISK_BAND_WEIGHTS.get(band, _RISK_BAND_WEIGHTS["balanced"])


def size_by_risk_band(picks: list[EnrichedPick], band: str) -> list[EnrichedPick]:
    """Assign uniform per-pick weight based on the risk band."""
    w = weight_pct_for_risk_band(band)
    for p in picks:
        p.weight_pct = w
    return picks


# ---------------------------------------------------------------------------
# Operator-feedback penalty (Stage 5)
# ---------------------------------------------------------------------------


def apply_feedback_penalty(
    picks: list[EnrichedPick],
    *,
    penalty: float,
    lookback_days: int,
    universe: str,
) -> list[EnrichedPick]:
    """Demote a pick's score when the operator thumbs-downed it within window.

    Importing db lazily so the harness module stays importable without UI extras.
    """
    if penalty >= 1.0 or not picks:
        return picks
    try:
        from dipdiver.ui import db
    except Exception:  # noqa: BLE001
        return picks
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
    try:
        with db.session() as s:
            negs = (
                s.query(db.UserFeedback)
                .filter(db.UserFeedback.universe == universe)
                .filter(db.UserFeedback.rating == -1)
                .all()
            )
            penalised = {
                f.symbol.upper() for f in negs
                if (f.date and f.date >= cutoff.isoformat())
            }
    except Exception:  # noqa: BLE001
        return picks
    if not penalised:
        return picks
    # Demote scores; re-rank afterward.
    for p in picks:
        if p.symbol in penalised:
            p.feedback_penalty_applied = True
    # Re-sort by adjusted score (apply penalty multiplicatively).
    picks.sort(
        key=lambda p: (p.score * (penalty if p.feedback_penalty_applied else 1.0)),
        reverse=True,
    )
    # Re-number ranks.
    for i, p in enumerate(picks):
        p.rank = i + 1
    return picks


def merge_watchlist(
    picks: list[EnrichedPick],
    *,
    universe: str,
    top_n: int,
) -> list[EnrichedPick]:
    """Surface watchlist symbols even if they aren't in the top-N.

    Lazy DB import — same reason as apply_feedback_penalty.
    """
    try:
        from dipdiver.ui import db
    except Exception:  # noqa: BLE001
        return picks
    try:
        with db.session() as s:
            entries = (
                s.query(db.WatchlistEntry)
                .filter(db.WatchlistEntry.universe == universe)
                .all()
            )
            wl_symbols = {e.symbol.upper() for e in entries}
    except Exception:  # noqa: BLE001
        return picks
    if not wl_symbols:
        return picks
    existing = {p.symbol for p in picks}
    # Mark already-present watchlist items
    for p in picks:
        if p.symbol in wl_symbols:
            p.on_watchlist = True
    # Append missing watchlist items as low-rank picks (score 0)
    for sym in sorted(wl_symbols - existing):
        picks.append(EnrichedPick(
            rank=len(picks) + 1, symbol=sym, score=0.0,
            signal_date=picks[0].signal_date if picks else "",
            universe=universe,
            weight_pct=0.0,
            on_watchlist=True,
        ))
    return picks
