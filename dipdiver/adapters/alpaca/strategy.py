"""TopkDropoutStrategy logic, shared between live (Alpaca) and intended for
keeping in lockstep with the Lean algorithm at
lean_projects/dipdiver_dow30_lightgbm/main.py.

We can't import from inside Lean's docker container, so the rebalance logic
lives in two places and MUST stay synchronised. If you change one, change the
other.
"""

from __future__ import annotations


def compute_target_holdings(
    scored: list[tuple[str, float]],
    current_holdings: set[str],
    topk: int,
    n_drop: int,
) -> tuple[set[str], set[str], set[str]]:
    """Apply TopkDropoutStrategy rules. Returns (target, adds, removes).

    `scored`: list of (symbol, score) ordered however. Higher score = stronger buy.
    `current_holdings`: set of symbols we currently hold.
    `topk`: portfolio size.
    `n_drop`: number of names to rotate per rebalance.

    Returns:
      target:  set of symbols we want to hold after rebalance
      adds:    target - current_holdings (names to open)
      removes: current_holdings - target (names to close)

    Surviving holdings (those neither added nor removed) carry over untouched —
    we do NOT rebalance their weights. This matches Qlib's strategy semantics
    and avoids per-day-drift churn that breaks backtest parity.
    """
    scored_desc = sorted(scored, key=lambda kv: kv[1], reverse=True)
    scores = {sym: score for sym, score in scored_desc}

    if not current_holdings:
        target = {sym for sym, _ in scored_desc[:topk]}
    else:
        # Drop bottom-N_DROP of held names (ranked by today's score).
        current_ranked = sorted(
            current_holdings,
            key=lambda t: scores.get(t, float("-inf")),
        )
        to_drop = set(current_ranked[:n_drop])

        # Add top-N_DROP from non-held universe.
        non_held_top = [
            sym for sym, _ in scored_desc
            if sym not in current_holdings
        ][:n_drop]
        to_add = set(non_held_top)

        target = (current_holdings - to_drop) | to_add

    adds = target - current_holdings
    removes = current_holdings - target
    return target, adds, removes
