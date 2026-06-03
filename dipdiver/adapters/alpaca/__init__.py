"""M3 step 6 — Alpaca paper-trading live runner.

Why a separate execution path from Lean: Lean's live trading requires a paid
QuantConnect subscription (Researcher seat + Live Node ~ $28/mo). Alpaca's
paper API is free. We accept the trade-off documented in ADR-004:

  * Backtest execution: Lean (battle-tested, structural backtest <-> live parity)
  * Live execution: this adapter (direct Alpaca REST via alpaca-py)

Both consume the same signals.csv, and both apply the same TopkDropoutStrategy
logic. Parity between the two is empirical (verified by running paper for 5
days and comparing to a Lean backtest over the same window).

If Lean live ever becomes available without the QC subscription, the runner
in scripts/m3_live_alpaca.py becomes obsolete; this adapter is the layer that
gets retired.
"""

from dipdiver.adapters.alpaca.strategy import compute_target_holdings

__all__ = ["compute_target_holdings"]
