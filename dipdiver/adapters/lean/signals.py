"""SignalSnapshot — the M1/M2 ↔ Lean handoff format.

A CSV with three columns: `date,symbol,score`. One row per (date, instrument)
pair from the test window. `score` is the Qlib model's raw prediction
(higher = stronger long signal).

Lean's Alpha Model reads this CSV daily and emits one Insight per non-null
score row. Portfolio Construction turns the score distribution into target
weights (top-k by score, equal-weighted, n_drop rotation).

The format is identical to what Qlib's `SignalRecord` writes as `pred.pkl`,
just dumped as CSV so Lean's data subsystem can consume it without needing
Python deserialisation inside the container.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SignalSnapshot:
    """One (date, symbol, score) datum."""

    date: str       # ISO date, e.g. "2024-01-02"
    symbol: str     # In-store symbol matching the Lean universe (e.g. "AAPL", "msft")
    score: float    # Raw predicted return — Lean's strategy ranks on this


def write_signal_csv(rows: Iterable[SignalSnapshot], target: Path) -> int:
    """Write rows to CSV. Returns the number of rows written."""
    target.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with target.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "symbol", "score"])
        for row in rows:
            w.writerow([row.date, row.symbol, f"{row.score:.6f}"])
            n += 1
    log.info("signals: wrote %d rows to %s", n, target)
    return n


def read_signal_csv(source: Path) -> list[SignalSnapshot]:
    """Read a CSV back into SignalSnapshot objects. For tests + verify scripts."""
    rows: list[SignalSnapshot] = []
    with source.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(SignalSnapshot(
                date=r["date"], symbol=r["symbol"], score=float(r["score"])
            ))
    return rows


def signals_from_qlib_pred(pred_path: Path) -> list[SignalSnapshot]:
    """Convert Qlib's pred.pkl (signal output) into SignalSnapshots.

    Qlib's pred.pkl is a pandas DataFrame indexed by (datetime, instrument)
    with one column 'score'. This is what SignalRecord writes after model fit.
    """
    import pandas as pd

    df = pd.read_pickle(pred_path)
    if "score" not in df.columns:
        raise ValueError(
            f"{pred_path} has no 'score' column; got {list(df.columns)}"
        )
    out: list[SignalSnapshot] = []
    for (dt, sym), row in df.iterrows():
        score = float(row["score"])
        if score != score:  # NaN
            continue
        out.append(SignalSnapshot(
            date=pd.Timestamp(dt).strftime("%Y-%m-%d"),
            symbol=str(sym),
            score=score,
        ))
    return out
