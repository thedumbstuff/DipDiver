"""Export M1 model signals to CSV for Lean to consume.

Re-runs the M1 fit + predict in-process (~1-2 min on dow30), then writes
the test-segment predictions to a SignalSnapshot CSV. Lean's algorithm
reads this CSV at runtime to know which symbols to hold each day.

Usage:
    python scripts/m3_export_signals.py --m1-config dow30_lightgbm.yaml

    # custom output (e.g. directly into a Lean project's data dir)
    python scripts/m3_export_signals.py \\
        --m1-config dow30_lightgbm.yaml \\
        --output lean_projects/dipdiver_dow30_lightgbm/data/signals.csv

The default output is data/signals/<m1-config-stem>.csv (gitignored).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dipdiver._paths import repo_root, resolve_provider_uri
from dipdiver.adapters.lean import SignalSnapshot, write_signal_csv
from dipdiver.brain.baselines.config import BaselineConfig, load_config


log = logging.getLogger(__name__)


def _qlib_region(region: str):
    """Mirror M1 runner's region mapping so we use the same data segmentation."""
    from qlib.config import REG_CN, REG_US

    return {"us": REG_US, "in": REG_US, "crypto": REG_US, "cn": REG_CN}.get(region, REG_US)


def export_signals(m1: BaselineConfig, output_path: Path) -> int:
    """Fit M1 model, predict on the test segment, write SignalSnapshot CSV.

    Returns the number of rows written. Skips NaN predictions (instruments
    with insufficient lookback at the start of the test window typically
    have one or two days of NaN).
    """
    import qlib
    from qlib.utils import init_instance_by_config

    from dipdiver.brain.baselines._qlib.task import build_task

    log.info("Initialising Qlib (provider=%s region=%s)",
             m1.qlib_provider_uri, m1.region)
    qlib.init(
        provider_uri=str(resolve_provider_uri(m1.qlib_provider_uri)),
        region=_qlib_region(m1.region),
    )

    log.info("Building task for %s (%s)", m1.name, m1.model)
    task = build_task(m1)
    model = init_instance_by_config(task["model"])
    dataset = init_instance_by_config(task["dataset"])

    log.info("Fitting model (this takes ~1-2 min for LightGBM, ~5-15 min for LSTM)")
    model.fit(dataset)

    log.info("Predicting on test segment %s -> %s", m1.test_start, m1.test_end)
    # Qlib model.predict(dataset) returns a pandas Series indexed by
    # (datetime, instrument). Defaults to the test segment.
    pred = model.predict(dataset)

    log.info("Got %d raw predictions", len(pred))
    snapshots: list[SignalSnapshot] = []
    n_nan = 0
    for idx, score in pred.items():
        if not isinstance(idx, tuple) or len(idx) != 2:
            continue
        dt, sym = idx
        if score != score:  # NaN check
            n_nan += 1
            continue
        snapshots.append(SignalSnapshot(
            date=dt.strftime("%Y-%m-%d") if hasattr(dt, "strftime") else str(dt),
            symbol=str(sym),
            score=float(score),
        ))
    if n_nan:
        log.info("Skipped %d NaN predictions (typical lookback warmup)", n_nan)

    if not snapshots:
        raise RuntimeError(
            f"No usable predictions for {m1.name}. Check that the M1 data store "
            f"covers the test window {m1.test_start} -> {m1.test_end}."
        )

    n = write_signal_csv(snapshots, output_path)
    return n


def _default_output_for(m1_config_filename: str) -> Path:
    """data/signals/<stem>.csv at the repo root."""
    stem = m1_config_filename.replace(".yaml", "").replace(".yml", "")
    return repo_root() / "data" / "signals" / f"{stem}.csv"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--m1-config", required=True,
                        help="M1 baseline YAML filename, e.g. dow30_lightgbm.yaml")
    parser.add_argument("--output", type=Path, default=None,
                        help="CSV path. Default: data/signals/<m1-config-stem>.csv")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg_dir = repo_root() / "dipdiver" / "brain" / "baselines" / "configs"
    m1 = load_config(cfg_dir / args.m1_config)

    output = args.output if args.output else _default_output_for(args.m1_config)
    print(f"[m3-export] M1 config: {args.m1_config}")
    print(f"[m3-export]   universe:    {m1.universe}")
    print(f"[m3-export]   model:       {m1.model}")
    print(f"[m3-export]   test window: {m1.test_start} -> {m1.test_end}")
    print(f"[m3-export] output: {output}")
    print()

    n = export_signals(m1, output)

    print()
    print(f"[m3-export] DONE — wrote {n} signal rows to {output}")
    print(f"[m3-export] inspect: head -5 \"{output}\"  &&  wc -l \"{output}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
