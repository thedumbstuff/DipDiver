"""Orchestrate the propose → execute → record loop.

State is plain JSON on disk. Each loop writes loop_N.json the moment it's
done; a Ctrl-C anywhere mid-run leaves the partial transcript intact.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
from pathlib import Path

from dipdiver.brain.baselines.config import BaselineConfig
from dipdiver.brain.baselines.results import load_locked
from dipdiver.brain.m2.lite.executor import execute
from dipdiver.brain.m2.lite.proposer import ProposerConfig, propose
from dipdiver.brain.m2.lite.schema import LoopRecord

log = logging.getLogger(__name__)


# Preset providers — keep small. User can override individual fields by env.
PROVIDERS: dict[str, ProposerConfig] = {
    "deepseek": ProposerConfig(
        model="deepseek-v4-pro",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        pricing_tier="deepseek",
    ),
    "openai": ProposerConfig(
        model="gpt-4o",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        pricing_tier="openai_gpt4o",
    ),
}


def run_lite_loop(
    m1: BaselineConfig,
    output_dir: Path,
    max_loops: int = 5,
    cost_cap_usd: float = 5.0,
    provider: str = "deepseek",
) -> dict[str, object]:
    """Run the loop end-to-end. Returns a summary dict suitable for JSON dump.

    Side effects:
      - writes one loop_N.json per loop into output_dir
      - writes summary.json into output_dir at the end (or on abort)
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}; known: {sorted(PROVIDERS)}")
    proposer_cfg = PROVIDERS[provider]

    m1_lock = load_locked(m1.config_hash)
    log.info(f"m2-lite: M1 baseline Sharpe={m1_lock.sharpe:+.3f} "
             f"AnnRet={m1_lock.annualised_return:+.2%}")
    log.info(f"m2-lite: budget={max_loops} loops, cap=${cost_cap_usd:.2f}, provider={provider}")

    loops: list[LoopRecord] = []
    total_cost = 0.0

    for idx in range(max_loops):
        if total_cost >= cost_cap_usd:
            log.warning(f"m2-lite: hit cost cap ${cost_cap_usd:.2f} at loop {idx}; stopping")
            break

        log.info(f"m2-lite: loop {idx}/{max_loops} "
                 f"(spent ${total_cost:.3f} / ${cost_cap_usd:.2f})")

        # 1. Propose
        try:
            proposal, in_tok, out_tok, cost, propose_wall = propose(
                proposer_cfg,
                universe=m1.universe, region=m1.region,
                train_start=m1.train_start, test_start=m1.test_start, test_end=m1.test_end,
                benchmark=m1.benchmark,
                m1_sharpe=m1_lock.sharpe, m1_ann_return=m1_lock.annualised_return,
                prior_loops=loops,
            )
        except Exception as e:  # noqa: BLE001
            log.error(f"m2-lite: propose failed at loop {idx}: {e}")
            rec = LoopRecord(index=idx, error=f"propose: {e}")
            _persist_loop(output_dir, rec)
            break

        log.info(f"m2-lite: loop {idx} proposal: {len(proposal.factors)} factor(s) — "
                 f"{', '.join(f.name for f in proposal.factors)}")

        # 2. Execute
        t0 = time.time()
        try:
            metrics = execute(m1, proposal.factors,
                              experiment_name=f"m2_lite_loop_{idx}")
            err = None
        except Exception as e:  # noqa: BLE001
            log.error(f"m2-lite: execute failed at loop {idx}: {e}")
            metrics, err = None, f"execute: {type(e).__name__}: {e}"
        execute_wall = time.time() - t0

        rec = LoopRecord(
            index=idx, proposal=proposal, metrics=metrics, error=err,
            llm_input_tokens=in_tok, llm_output_tokens=out_tok, llm_cost_usd=cost,
            wall_seconds=propose_wall + execute_wall,
        )
        _persist_loop(output_dir, rec)
        loops.append(rec)
        total_cost += cost

        if metrics:
            log.info(f"m2-lite: loop {idx} Sharpe={metrics.sharpe:+.3f} "
                     f"AnnRet={metrics.annualised_return:+.2%} (cost ${cost:.3f})")

    return _persist_summary(output_dir, m1, loops, m1_lock.sharpe,
                            m1_lock.annualised_return, total_cost)


def _persist_loop(output_dir: Path, rec: LoopRecord) -> None:
    path = output_dir / f"loop_{rec.index}.json"
    path.write_text(rec.model_dump_json(indent=2), encoding="utf-8")


def _persist_summary(
    output_dir: Path,
    m1: BaselineConfig,
    loops: list[LoopRecord],
    m1_sharpe: float,
    m1_ann_return: float,
    total_cost: float,
) -> dict[str, object]:
    successful = [r for r in loops if r.metrics is not None]
    best = max(successful, key=lambda r: r.metrics.sharpe, default=None) if successful else None

    summary: dict[str, object] = {
        "timestamp_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        # Universe identity so verify and downstream tools can tell which run
        # corresponds to which baseline without parsing the directory name.
        "m1_config_name": m1.name,
        "universe": m1.universe,
        "region": m1.region,
        "benchmark": m1.benchmark,
        "test_window": {"start": m1.test_start, "end": m1.test_end},
        "m1_baseline": {
            "sharpe": m1_sharpe,
            "annualised_return": m1_ann_return,
            "config_hash": m1.config_hash,
        },
        "n_loops_run": len(loops),
        "n_loops_successful": len(successful),
        "total_cost_usd": round(total_cost, 4),
        "total_input_tokens": sum(r.llm_input_tokens for r in loops),
        "total_output_tokens": sum(r.llm_output_tokens for r in loops),
        "best_loop_index": best.index if best else None,
    }
    if best:
        m = best.metrics  # type: ignore[union-attr]
        summary["best"] = {
            "sharpe": m.sharpe,
            "annualised_return": m.annualised_return,
            "max_drawdown": m.max_drawdown,
            "excess_return": m.excess_return,
            "delta_sharpe_vs_m1": m.sharpe - m1_sharpe,
            "delta_ann_return_vs_m1": m.annualised_return - m1_ann_return,
            "factors": [{"name": f.name, "expression": f.expression}
                        for f in best.proposal.factors],
            "hypothesis": best.proposal.hypothesis,
        }

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    return summary
