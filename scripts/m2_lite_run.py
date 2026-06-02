"""Run m2-lite — our LLM factor-proposer loop. No rdagent, no docker.

Usage (anywhere with the brain extras installed + a DeepSeek/OpenAI key):
    set -a; source .env.m2; set +a
    python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml
    python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml --loops 8 --cap 2.0
    python scripts/m2_lite_run.py --m1-config dow30_lightgbm.yaml --provider openai
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

from dipdiver._paths import repo_root
from dipdiver.brain.baselines.config import load_config as load_m1_config
from dipdiver.brain.m2.lite import PROVIDERS, run_lite_loop


def _load_env_file(path: Path) -> int:
    """Tiny .env loader so users don't need to remember `set -a; source ...`.

    Only sets keys that aren't already in os.environ — explicit shell exports win.
    Values may be quoted; everything else is a literal string (no expansion).
    Returns the number of variables exported.
    """
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val
            n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-config", required=True,
                        help="M1 baseline YAML filename (e.g. dow30_lightgbm.yaml)")
    parser.add_argument("--loops", type=int, default=5, help="Max loops (default 5)")
    parser.add_argument("--cap", type=float, default=2.0,
                        help="Hard USD cap; loop aborts when exceeded (default $2.00)")
    parser.add_argument("--provider", default="deepseek",
                        choices=sorted(PROVIDERS), help="LLM provider")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Where to write logs (default: logs/m2_lite/<timestamp>)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    # Auto-load .env.m2 from repo root so the user doesn't need `source .env.m2`.
    env_path = repo_root() / ".env.m2"
    n_loaded = _load_env_file(env_path)
    if n_loaded:
        print(f"[m2-lite] loaded {n_loaded} variable(s) from {env_path.name}")
    elif not env_path.exists():
        print(f"[m2-lite] no .env.m2 at {env_path} — relying on shell environment")

    cfg_dir = repo_root() / "dipdiver" / "brain" / "baselines" / "configs"
    m1 = load_m1_config(cfg_dir / args.m1_config)

    out = args.output_dir
    if out is None:
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = repo_root() / "logs" / "m2_lite" / f"{args.m1_config.replace('.yaml','')}_{ts}"

    print(f"[m2-lite] m1: {args.m1_config}")
    print(f"[m2-lite] provider: {args.provider}")
    print(f"[m2-lite] loops: {args.loops}  cost cap: ${args.cap:.2f}")
    print(f"[m2-lite] output: {out}")
    print()

    summary = run_lite_loop(
        m1=m1, output_dir=out,
        max_loops=args.loops, cost_cap_usd=args.cap, provider=args.provider,
    )

    print()
    print("=" * 80)
    print("[m2-lite] DONE")
    print("=" * 80)
    print(f"  loops run/successful: {summary['n_loops_run']}/{summary['n_loops_successful']}")
    print(f"  total cost: ${summary['total_cost_usd']:.3f} "
          f"({summary['total_input_tokens']:,} in + {summary['total_output_tokens']:,} out)")
    if summary.get("best"):
        b = summary["best"]  # type: ignore[index]
        print(f"  best loop: #{summary['best_loop_index']} sharpe={b['sharpe']:+.3f} "
              f"ann_ret={b['annualised_return']:+.2%}")
        print(f"  delta vs M1: sharpe={b['delta_sharpe_vs_m1']:+.3f} "
              f"ann_ret={b['delta_ann_return_vs_m1']:+.2%}")
        print(f"  factors: {', '.join(f['name'] for f in b['factors'])}")
    else:
        print("  no successful loops")
    print(f"  summary: {out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
