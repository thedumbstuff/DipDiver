"""Stage 5 / M12 — Persona accuracy scorecards.

For each persona, joins VetoOutcomeEvent counterfactuals with OverrideDecision
to show whether vetoes saved money and whether operator overrides made money.

Example narrative the page surfaces:
    risk persona vetoed 12 buys, operator overrode 4, 3 of those 4 made money
    → risk persona over-vetos in volatile markets.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from dipdiver._paths import ui_scoreboard_path
from dipdiver.harness.scoreboard import VetoOutcomeEvent, read_events
from dipdiver.ui import db
from dipdiver.ui.helpers import template_ctx


router = APIRouter()


@dataclass
class PersonaScorecard:
    persona: str
    vetoes: int = 0
    vetoes_with_outcome: int = 0
    veto_regret_avg_pct: float = 0.0  # +ve = vetoes cost money on average
    overrides: int = 0
    override_wins: int = 0  # outcomes where override symbol made money
    examples: list[dict] = field(default_factory=list)


def _load_persona_scorecards() -> list[PersonaScorecard]:
    sb_path = ui_scoreboard_path()
    events = read_events(sb_path)
    veto_events_by_sym: dict[tuple[str, str, str], VetoOutcomeEvent] = {}
    for e in events:
        if isinstance(e, VetoOutcomeEvent):
            veto_events_by_sym[(e.date, e.universe, e.symbol.upper())] = e

    # The scoreboard's CommitteeVerdictSummary doesn't carry per-persona who
    # vetoed. We attribute vetoes to "risk" by default (single-vote veto rule
    # in M5) and to the aggregate "committee" otherwise. M14 will record the
    # per-persona breakdown.
    persona_vetoes: dict[str, list[VetoOutcomeEvent]] = defaultdict(list)
    for e in events:
        if e.event_type != "day_submitted":
            continue
        if not getattr(e, "committee_active", False):
            continue
        for v in e.committee_verdicts:
            if v.approved or v.direction != "buy":
                continue
            outcome = veto_events_by_sym.get((e.date, e.universe, v.symbol.upper()))
            attribution = "risk" if v.n_veto >= 2 else "committee"
            if outcome is not None:
                persona_vetoes[attribution].append(outcome)

    # Override stats
    with db.session() as s:
        overrides = s.query(db.OverrideDecision).all()
        override_rows = [
            {
                "date": o.date, "universe": o.universe, "symbol": o.symbol,
                "original": o.original_decision, "new": o.new_decision,
                "reason": o.reason,
            }
            for o in overrides
        ]

    scorecards: dict[str, PersonaScorecard] = {}
    for persona, outcomes in persona_vetoes.items():
        s = scorecards.setdefault(persona, PersonaScorecard(persona=persona))
        s.vetoes_with_outcome = len(outcomes)
        if outcomes:
            s.veto_regret_avg_pct = (
                sum(o.counterfactual_pnl_pct for o in outcomes) / len(outcomes)
            )
        # Examples — best/worst veto for context
        sorted_by_regret = sorted(outcomes, key=lambda o: o.counterfactual_pnl_pct)
        s.examples = [
            {"symbol": o.symbol, "date": o.date, "regret_pct": round(o.counterfactual_pnl_pct, 4)}
            for o in (sorted_by_regret[-3:][::-1] + sorted_by_regret[:1])
        ][:4]

    # Overrides attributed similarly
    for row in override_rows:
        # When operator overrode a "vetoed" → "approved", check if outcome was positive
        if row["original"] != "vetoed" or row["new"] != "approved":
            continue
        sc = scorecards.setdefault("risk", PersonaScorecard(persona="risk"))
        sc.overrides += 1
        outcome = veto_events_by_sym.get((row["date"], row["universe"], row["symbol"]))
        if outcome and outcome.counterfactual_pnl_pct > 0:
            sc.override_wins += 1

    return sorted(scorecards.values(), key=lambda s: s.persona)


@router.get("/persona-accuracy", response_class=HTMLResponse)
async def persona_accuracy_page(request: Request):
    from dipdiver.ui.app import templates

    cards = _load_persona_scorecards()
    ctx = template_ctx(request, cards=cards)
    return templates.TemplateResponse(request, "persona_accuracy.html", ctx)
