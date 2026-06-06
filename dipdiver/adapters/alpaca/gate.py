"""Stage 4 / M11 — live-trading safety gate.

Before any code path can talk to a live (non-paper) broker API, this gate
must pass for the strategy in question. The criteria mirror VALIDATION.md:

  forward_eval_days >= 60   — paper exposure long enough to trust the curve
  sharpe              > 1.0 — risk-adjusted return clears bench
  max_dd              < 0.10 — drawdown stayed shallow
  hit_rate            > 0.50 — wins out-numbered losses

Plus universe-broker compatibility: a strategy on `world_indices` cannot run
live on Alpaca regardless of metrics.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from dipdiver.harness.scoreboard import (
    DaySubmittedEvent,
    PnlSettledEvent,
    ScoreboardEvent,
    read_events,
)

log = logging.getLogger(__name__)


# Universes the Alpaca adapter can actually trade live. World indices, crypto
# (via Alpaca crypto is separate API), nifty50 are research-only here.
SUPPORTED_UNIVERSES: frozenset[str] = frozenset({"dow30", "sp500"})


@dataclass
class GateCriterion:
    name: str
    threshold: float | str
    actual: float | str | None
    passed: bool
    message: str = ""


@dataclass
class GateResult:
    strategy_id: str
    passed: bool
    criteria: list[GateCriterion] = field(default_factory=list)
    evaluated_utc: str = ""


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def _strategy_events(
    events: list[ScoreboardEvent], strategy_id: str
) -> tuple[list[DaySubmittedEvent], list[PnlSettledEvent]]:
    submitted = [e for e in events if e.event_type == "day_submitted" and e.strategy_id == strategy_id]
    pnl = [e for e in events if e.event_type == "pnl_settled" and e.strategy_id == strategy_id]
    return submitted, pnl  # type: ignore[return-value]


def _forward_eval_days(submitted: list[DaySubmittedEvent], pnl: list[PnlSettledEvent]) -> int:
    """Count distinct dates that have BOTH a submission and a settled P&L."""
    have_pnl = {e.date for e in pnl}
    have_sub = {e.date for e in submitted}
    return len(have_pnl & have_sub)


def _hit_rate(pnl: list[PnlSettledEvent]) -> float:
    if not pnl:
        return 0.0
    wins = sum(1 for e in pnl if (e.realised_pnl_usd or 0.0) > 0)
    return wins / len(pnl)


def _sharpe_from_pnl(pnl: list[PnlSettledEvent]) -> float:
    """Daily-PnL Sharpe — simple proxy, not accounting for risk-free rate.

    Annualised by sqrt(252). When fewer than 5 days or zero variance, returns 0.
    """
    if len(pnl) < 5:
        return 0.0
    rets = []
    for e in pnl:
        denom = (e.equity_at_close or 0.0) - (e.realised_pnl_usd or 0.0) - (e.unrealised_pnl_usd or 0.0)
        # Approximate "prior equity"; fall back to equity if denom is bogus.
        prior = denom if denom > 1 else (e.equity_at_close or 1.0)
        rets.append((e.realised_pnl_usd + e.unrealised_pnl_usd) / prior if prior else 0.0)
    if not rets:
        return 0.0
    n = len(rets)
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / n
    if var <= 0:
        return 0.0
    std = var ** 0.5
    return (mean / std) * (252 ** 0.5)


def _max_dd_from_pnl(pnl: list[PnlSettledEvent]) -> float:
    if not pnl:
        return 0.0
    sorted_pnl = sorted(pnl, key=lambda e: e.date)
    peak = float("-inf")
    max_dd = 0.0
    for e in sorted_pnl:
        eq = e.equity_at_close or 0.0
        peak = max(peak, eq)
        if peak > 0:
            dd = (eq - peak) / peak
            max_dd = min(max_dd, dd)
    return abs(max_dd)


# ---------------------------------------------------------------------------
# Universe inference
# ---------------------------------------------------------------------------


def _universe_of(strategy_id: str, submitted: list[DaySubmittedEvent]) -> str | None:
    """Strategy IDs are encoded `<universe>_<model>[_committee]`.
    Cross-check with the most recent submission for safety.
    """
    if submitted:
        return submitted[-1].universe
    # Fall back to splitting the ID
    parts = strategy_id.split("_", 1)
    return parts[0] if parts else None


# ---------------------------------------------------------------------------
# Main check
# ---------------------------------------------------------------------------


class LiveTradingGate:
    """Stateless validator. Construct with a strategy_id, call `.check()`."""

    def __init__(
        self,
        strategy_id: str,
        *,
        events: list[ScoreboardEvent] | None = None,
        thresholds: dict | None = None,
    ) -> None:
        self.strategy_id = strategy_id
        if events is None:
            try:
                from dipdiver._paths import ui_scoreboard_path
                self._events = read_events(ui_scoreboard_path())
            except Exception:  # noqa: BLE001
                self._events = read_events()
        else:
            self._events = events
        self._thresholds = {
            "forward_eval_days_min": 60,
            "sharpe_min": 1.0,
            "max_dd_max": 0.10,
            "hit_rate_min": 0.50,
        }
        if thresholds:
            self._thresholds.update(thresholds)

    def check(self) -> GateResult:
        submitted, pnl = _strategy_events(self._events, self.strategy_id)
        criteria: list[GateCriterion] = []

        # Eligibility: universe compatibility
        universe = _universe_of(self.strategy_id, submitted)
        universe_ok = (universe is not None) and (universe in SUPPORTED_UNIVERSES)
        criteria.append(GateCriterion(
            name="universe",
            threshold=", ".join(sorted(SUPPORTED_UNIVERSES)),
            actual=universe,
            passed=universe_ok,
            message=(
                "" if universe_ok else
                f"universe {universe!r} is research-only on this broker adapter"
            ),
        ))

        # Forward-eval days
        days = _forward_eval_days(submitted, pnl)
        days_ok = days >= self._thresholds["forward_eval_days_min"]
        criteria.append(GateCriterion(
            name="forward_eval_days",
            threshold=self._thresholds["forward_eval_days_min"],
            actual=days,
            passed=days_ok,
            message=(
                "" if days_ok else
                f"only {days} settled days; need {self._thresholds['forward_eval_days_min']}"
            ),
        ))

        # Performance metrics — only compute if we have any PnL at all
        sharpe = _sharpe_from_pnl(pnl)
        sharpe_ok = sharpe >= self._thresholds["sharpe_min"]
        criteria.append(GateCriterion(
            name="sharpe",
            threshold=self._thresholds["sharpe_min"],
            actual=round(sharpe, 3),
            passed=sharpe_ok,
        ))

        mdd = _max_dd_from_pnl(pnl)
        mdd_ok = mdd <= self._thresholds["max_dd_max"]
        criteria.append(GateCriterion(
            name="max_dd",
            threshold=self._thresholds["max_dd_max"],
            actual=round(mdd, 3),
            passed=mdd_ok,
        ))

        hit = _hit_rate(pnl)
        hit_ok = hit >= self._thresholds["hit_rate_min"]
        criteria.append(GateCriterion(
            name="hit_rate",
            threshold=self._thresholds["hit_rate_min"],
            actual=round(hit, 3),
            passed=hit_ok,
        ))

        passed = all(c.passed for c in criteria)
        result = GateResult(
            strategy_id=self.strategy_id,
            passed=passed,
            criteria=criteria,
            evaluated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        # Audit row (best-effort — UI db may not exist yet on standalone CLI use).
        try:
            from dipdiver.ui import db
            with db.session() as s:
                s.add(db.LiveGateAudit(
                    evaluated_utc=datetime.now(timezone.utc),
                    strategy_id=self.strategy_id,
                    passed=passed,
                    criteria_json=json.dumps([
                        {
                            "name": c.name, "threshold": c.threshold,
                            "actual": c.actual, "passed": c.passed,
                            "message": c.message,
                        }
                        for c in criteria
                    ]),
                    invoked_by="LiveTradingGate.check",
                ))
        except Exception as e:  # noqa: BLE001
            log.debug("could not write LiveGateAudit row: %s", e)
        return result


class LiveModeNotAllowedError(RuntimeError):
    """Raised by AlpacaClient(mode='live') when the gate would fail."""

    def __init__(self, result: GateResult, missing_env: str | None = None) -> None:
        self.result = result
        self.missing_env = missing_env
        msg = (
            f"live mode not allowed for {result.strategy_id}: gate failed. "
            + (f"Also missing env: {missing_env}. " if missing_env else "")
            + "Failing criteria: "
            + "; ".join(
                f"{c.name} (got {c.actual}, need {c.threshold})"
                for c in result.criteria if not c.passed
            )
        )
        super().__init__(msg)
