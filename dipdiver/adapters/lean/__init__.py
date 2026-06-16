"""M3 — Lean execution chassis adapter.

Translates DipDiver's signal output (Qlib model predictions or m2-lite
factors) into a format Lean's algorithm framework can consume.

Architecture (ADR-004):

    M1/M2 (Qlib) ── pred.pkl ──┐
                               ▼
                    SignalSnapshot CSV
                               │
                               ▼
                    Lean Alpha Model (reads CSV daily)
                               │
                               ▼
                    Lean Portfolio Construction → Execution → Brokerage

We deliberately keep Qlib outside Lean's docker container. The hand-off is a
plain CSV of (date, symbol, score) rows produced offline. This:
  - avoids the rdagent-style "two environments fighting each other" failure
  - makes the Lean side trivially replayable (just feed it different CSVs)
  - means Lean's backtest is testing the EXECUTION path, not the alpha model
"""

from dipdiver.adapters.lean.signals import SignalSnapshot, write_signal_csv

__all__ = ["SignalSnapshot", "write_signal_csv"]
