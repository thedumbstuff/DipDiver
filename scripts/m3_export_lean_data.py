"""Convert our Qlib US data store into Lean's daily equity format.

Lean expects each US equity's daily bars at:
    <data_folder>/equity/usa/daily/<lowercase_symbol>.zip

Inside each zip: one CSV with rows
    YYYYMMDD 00:00,open*10000,high*10000,low*10000,close*10000,volume

Prices are stored as integers (scaled x10000) so Lean's parser can avoid
floating-point. Adjusted prices are what we have in Qlib (back-adjusted close);
that's what we feed Lean — gives backtest-consistent returns vs M1.

Usage:
    python scripts/m3_export_lean_data.py
    python scripts/m3_export_lean_data.py --universe dow30 --output-dir lean_projects/data
    python scripts/m3_export_lean_data.py --universe world_indices  # for later use
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import zipfile
from pathlib import Path

from dipdiver._paths import repo_root, resolve_provider_uri
from dipdiver.brain.baselines.universes import get_universe


log = logging.getLogger(__name__)


def _format_row(date_iso: str, o: float, h: float, l: float, c: float, v: float) -> str:
    """Lean's daily-bar row format: YYYYMMDD 00:00,o,h,l,c,v (prices x10000)."""
    yyyymmdd = date_iso.replace("-", "")
    return (
        f"{yyyymmdd} 00:00,"
        f"{int(round(o * 10000))},"
        f"{int(round(h * 10000))},"
        f"{int(round(l * 10000))},"
        f"{int(round(c * 10000))},"
        f"{int(round(v))}"
    )


def export_one_symbol(
    qlib_symbol: str,
    lean_symbol_lower: str,
    output_dir: Path,
    start: str,
    end: str,
) -> int:
    """Read OHLCV for one instrument from Qlib, write a Lean daily ZIP. Returns row count."""
    from qlib.data import D

    fields = ["$open", "$high", "$low", "$close", "$volume"]
    df = D.features([qlib_symbol], fields, start, end, freq="day")
    if df is None or df.empty:
        log.warning("no data for %s in window %s..%s", qlib_symbol, start, end)
        return 0
    # Drop instrument level — single instrument here.
    df = df.droplevel(0)
    df.columns = ["open", "high", "low", "close", "volume"]
    df = df.dropna(subset=["close"])

    rows = []
    for ts, r in df.iterrows():
        try:
            rows.append(_format_row(
                ts.strftime("%Y-%m-%d"),
                float(r["open"]), float(r["high"]),
                float(r["low"]), float(r["close"]), float(r["volume"]),
            ))
        except (ValueError, TypeError):
            continue
    if not rows:
        log.warning("no usable rows for %s", qlib_symbol)
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / f"{lean_symbol_lower}.zip"
    csv_name = f"{lean_symbol_lower}.csv"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, "\n".join(rows) + "\n")
    return len(rows)


def export_universe(
    universe_name: str,
    output_dir: Path,
    start: str = "2014-01-01",
    end: str = "2026-12-31",
) -> dict[str, int]:
    """Walk a DipDiver universe and write a Lean ZIP per instrument."""
    import qlib
    from qlib.config import REG_US

    universe = get_universe(universe_name)
    # Use our M1 US store via dipdiver._paths.resolve_provider_uri.
    # All four of our universes use the US calendar (qlib region).
    if universe.region != "us":
        log.warning(
            "Lean's equity/usa/daily layout assumes US-market data. Universe "
            "%s has region=%s; output will land at the same path but Lean's "
            "market-hours model may differ from local-market trading hours.",
            universe_name, universe.region,
        )

    # Map our M1 region to a Qlib provider URI; reuse the universe-specific dir.
    provider_dirs = {
        "dow30": "data/qlib/us_data",
        "nifty50": "data/qlib/in_data",
        "crypto": "data/qlib/crypto_data",
        "world_indices": "data/qlib/world_data",
    }
    provider = resolve_provider_uri(provider_dirs[universe_name])
    log.info("init Qlib provider=%s", provider)
    qlib.init(provider_uri=str(provider), region=REG_US)

    results: dict[str, int] = {}
    for sym in universe.instruments:
        # Qlib stores symbols lowercase. Lean uses lowercase under equity/usa/daily/.
        qlib_sym = sym.lower()
        lean_sym = sym.lower()
        n = export_one_symbol(qlib_sym, lean_sym, output_dir, start, end)
        results[sym] = n
        log.info("  %s -> %s.zip (%d rows)", sym, lean_sym, n)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="dow30",
                        choices=["dow30", "nifty50", "crypto", "world_indices"])
    parser.add_argument(
        "--output-dir", type=Path, default=None,
        help="Default: lean_projects/data/equity/usa/daily/",
    )
    parser.add_argument("--start", default="2014-01-01")
    parser.add_argument("--end", default="2026-12-31")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.output_dir is None:
        out = repo_root() / "lean_projects" / "data" / "equity" / "usa" / "daily"
    else:
        out = args.output_dir
    print(f"[m3-data] universe: {args.universe}")
    print(f"[m3-data] window:   {args.start} -> {args.end}")
    print(f"[m3-data] output:   {out}")
    print()

    results = export_universe(args.universe, out, args.start, args.end)

    n_ok = sum(1 for v in results.values() if v > 0)
    n_total_rows = sum(results.values())
    print()
    print(f"[m3-data] DONE — {n_ok}/{len(results)} symbols, {n_total_rows} total rows")
    if n_ok < len(results):
        zero = [s for s, v in results.items() if v == 0]
        print(f"[m3-data] NO DATA for: {', '.join(zero)}")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
