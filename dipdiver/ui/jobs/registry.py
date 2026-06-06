"""Centralised registry of background jobs.

Each entry:
  job_id        — stable id (used in DB + APScheduler)
  description   — one-liner shown on /schedule
  default_cron  — UTC cron expression used if no DB entry exists
  func          — callable invoked by the scheduler (no args)

Jobs themselves live in sibling modules (nightly.py, pnl_settle.py, etc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class JobDef:
    job_id: str
    description: str
    default_cron: str
    func: Callable[[], dict]


def _registry() -> list[JobDef]:
    # Imports here so the module loads even when individual job impls are
    # unimportable (e.g. alpaca-py missing on a stripped install).
    from dipdiver.ui.jobs import (
        m1_retrain,
        m2_lite,
        nightly,
        pnl_settle,
        scoreboard_render,
        signal_refresh,
        veto_backfill,
    )

    return [
        JobDef(
            job_id="nightly_run",
            description="Run M3 live (Alpaca paper) + promote to scoreboard",
            default_cron="35 14 * * 1-5",
            func=nightly.run,
        ),
        JobDef(
            job_id="m1_retrain",
            description="Roll M1 training windows + relock (monthly)",
            default_cron="0 4 1 * *",
            func=m1_retrain.run,
        ),
        JobDef(
            job_id="pnl_settle",
            description="Fetch yesterday's Alpaca portfolio history → PnlSettledEvent",
            default_cron="30 9 * * 2-6",
            func=pnl_settle.run,
        ),
        JobDef(
            job_id="veto_backfill",
            description="Look up T+5 prices for vetoed buys → VetoOutcomeEvent",
            default_cron="0 6 * * 1-5",
            func=veto_backfill.run,
        ),
        JobDef(
            job_id="m2_lite_weekly",
            description="Weekly LLM factor discovery on enabled universes",
            default_cron="0 3 * * 0",
            func=m2_lite.run,
        ),
        JobDef(
            job_id="signal_refresh",
            description="Regenerate data/signals/*.csv from current M1 models",
            default_cron="0 12 * * 0",  # Sunday noon UTC
            func=signal_refresh.run,
        ),
        JobDef(
            job_id="scoreboard_render",
            description="Render scoreboard.jsonl → rendered/SCOREBOARD.md",
            default_cron="0 15 * * 1-5",
            func=scoreboard_render.run,
        ),
    ]


def all_jobs() -> list[JobDef]:
    return _registry()


def get_job(job_id: str) -> JobDef | None:
    for j in _registry():
        if j.job_id == job_id:
            return j
    return None
