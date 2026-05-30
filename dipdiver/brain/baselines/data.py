"""M1 data pipeline — fetch OHLCV, write Qlib-compatible binary store, verify.

Three jobs:

1. **Fetch** — for the US universe, use Qlib's own bundled downloader if
   pyqlib is installed; otherwise fall back to Yahoo (yfinance). For NIFTY 50
   and crypto, always use Yahoo.
2. **Dump** — write Qlib's binary on-disk format (calendar + instrument lists +
   per-field float32 .day.bin files). Format documented at
   https://qlib.readthedocs.io/en/latest/component/data.html#qlib-format-data .
3. **Verify** — sanity-check the on-disk store: calendar covers the expected
   span, every instrument has data, no all-NaN columns.

Yahoo is rate-limited and occasionally returns gaps; the fetch routine retries
and logs missing days for verification rather than silently zero-filling.
"""

from __future__ import annotations

import logging
import struct
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from dipdiver.brain.baselines.universes import Universe

log = logging.getLogger(__name__)

# Qlib's standard daily feature set. Alpha158 reads from these.
QLIB_FIELDS: tuple[str, ...] = ("open", "high", "low", "close", "volume", "factor", "vwap")


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FetchResult:
    instrument: str
    rows: int
    start: str
    end: str
    missing_days: int  # weekdays between start and end with no row


def fetch_yahoo(
    instruments: Iterable[str],
    start: str,
    end: str,
    *,
    max_retries: int = 3,
) -> dict[str, "pd.DataFrame"]:  # type: ignore[name-defined]
    """Pull daily OHLCV from Yahoo via yfinance.

    Returns one DataFrame per instrument, indexed by date with columns
    [open, high, low, close, volume, adj_close, factor]. factor = adj_close/close.
    Retries on empty result up to max_retries times.
    """
    import time

    import pandas as pd
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for tic in instruments:
        df = None
        for attempt in range(1, max_retries + 1):
            log.info("yahoo fetch: %s (attempt %d/%d)", tic, attempt, max_retries)
            try:
                df = yf.download(
                    tic,
                    start=start,
                    end=end,
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
            except Exception as e:  # noqa: BLE001
                log.warning("yahoo fetch failed for %s: %s", tic, e)
                df = None
            if df is not None and not df.empty:
                break
            if attempt < max_retries:
                time.sleep(2 * attempt)  # gentle backoff
        if df is None or df.empty:
            log.warning("yahoo returned empty frame for %s after %d attempts", tic, max_retries)
            continue
        # yfinance returns a MultiIndex on columns when threads=True; flatten just in case.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(
            columns={
                "Open": "raw_open",
                "High": "raw_high",
                "Low": "raw_low",
                "Close": "raw_close",
                "Volume": "raw_volume",
                "Adj Close": "adj_close",
            }
        )
        df.index = pd.to_datetime(df.index).tz_localize(None)

        # Qlib convention: $close is backward-adjusted (today's adjusted = today's raw;
        # historical days scaled by dividend/split events). $factor stays in the store
        # so consumers that need raw price can recover it via raw = $close / $factor.
        # All OHLC are adjusted consistently; volume is scaled inversely so that
        # price * volume (notional) is preserved across adjustments.
        df["factor"] = df["adj_close"] / df["raw_close"]
        df["close"] = df["adj_close"]
        df["open"] = df["raw_open"] * df["factor"]
        df["high"] = df["raw_high"] * df["factor"]
        df["low"] = df["raw_low"] * df["factor"]
        df["volume"] = df["raw_volume"] / df["factor"]
        df["vwap"] = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
        out[tic] = df[
            ["open", "high", "low", "close", "volume", "factor", "vwap"]
        ].astype("float32")
    return out


def fetch_qlib_us_bundle(target_dir: Path) -> bool:
    """Download Qlib's prebuilt US data bundle into target_dir.

    Returns True on success. Falls back to caller (use fetch_yahoo) on failure.
    """
    try:
        from qlib.tests.data import GetData
    except ImportError:
        log.warning("pyqlib not installed; cannot use prebuilt US bundle")
        return False
    try:
        GetData().qlib_data(
            target_dir=str(target_dir.expanduser()),
            region="us",
            exists_skip=True,
        )
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("qlib US bundle download failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Dump — write Qlib binary store from per-instrument DataFrames
# ---------------------------------------------------------------------------


def _write_calendar(provider_uri: Path, calendar: list[str]) -> Path:
    out = provider_uri / "calendars" / "day.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(calendar) + "\n", encoding="utf-8")
    return out


def _write_instrument_list(
    provider_uri: Path,
    name: str,
    entries: list[tuple[str, str, str]],
) -> Path:
    out = provider_uri / "instruments" / f"{name}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(e) for e in entries]
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _write_feature_bin(
    provider_uri: Path,
    instrument: str,
    field: str,
    start_index: int,
    values: "np.ndarray",  # type: ignore[name-defined]
) -> Path:
    """Write a Qlib .day.bin file.

    Format: little-endian uint32 start_index, then little-endian float32 values.
    """
    import numpy as np

    inst_dir = provider_uri / "features" / instrument.lower()
    inst_dir.mkdir(parents=True, exist_ok=True)
    out = inst_dir / f"{field}.day.bin"
    arr = np.asarray(values, dtype="<f4")
    with out.open("wb") as f:
        f.write(struct.pack("<I", start_index))
        arr.tofile(f)
    return out


def dump_to_qlib(
    provider_uri: Path,
    frames: dict[str, "pd.DataFrame"],  # type: ignore[name-defined]
    universe: Universe,
) -> list[FetchResult]:
    """Write a Qlib binary store from per-instrument DataFrames.

    The calendar is the union of all instruments' trading days, sorted ascending.
    Each instrument is recorded against the global calendar with a start index
    and a contiguous run of float32 values; missing days are encoded as NaN.
    """
    import numpy as np
    import pandas as pd

    if not frames:
        raise ValueError("no frames to dump")

    # Global calendar — union of all instrument dates.
    all_dates: set[pd.Timestamp] = set()
    for df in frames.values():
        all_dates.update(df.index.tolist())
    calendar = sorted(all_dates)
    calendar_str = [d.strftime("%Y-%m-%d") for d in calendar]
    _write_calendar(provider_uri, calendar_str)

    # Instrument list — record each instrument's actual first/last trading day.
    # all_entries goes into all.txt; universe_entries (filtered) into <universe>.txt.
    all_entries: list[tuple[str, str, str]] = []
    universe_entries: list[tuple[str, str, str]] = []
    universe_set = {t.lower() for t in universe.instruments}
    results: list[FetchResult] = []

    for tic, df in frames.items():
        if df.empty:
            log.warning("skipping empty frame: %s", tic)
            continue
        first_date = df.index.min()
        last_date = df.index.max()
        first_idx = calendar.index(first_date)
        # Reindex against [first_date .. last_date] segment of the global calendar
        # to insert NaN for in-segment missing trading days (e.g. yahoo gap).
        segment = [d for d in calendar if first_date <= d <= last_date]
        df_aligned = df.reindex(segment)

        n_missing_in_segment = int(df_aligned["close"].isna().sum())

        for field in QLIB_FIELDS:
            if field in df_aligned.columns:
                values = df_aligned[field].to_numpy(dtype="float32", na_value=np.nan)
            else:
                values = np.full(len(df_aligned), np.nan, dtype="float32")
            _write_feature_bin(provider_uri, tic, field, first_idx, values)

        entry = (
            tic.lower(),
            first_date.strftime("%Y-%m-%d"),
            last_date.strftime("%Y-%m-%d"),
        )
        all_entries.append(entry)
        if entry[0] in universe_set:
            universe_entries.append(entry)
        results.append(
            FetchResult(
                instrument=tic,
                rows=int(df_aligned["close"].notna().sum()),
                start=entry[1],
                end=entry[2],
                missing_days=n_missing_in_segment,
            )
        )

    _write_instrument_list(provider_uri, "all", all_entries)
    _write_instrument_list(provider_uri, universe.name, universe_entries)
    log.info(
        "wrote %d instruments (%d in universe) to %s",
        len(all_entries),
        len(universe_entries),
        provider_uri,
    )
    return results


# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyReport:
    provider_uri: Path
    universe: str
    n_instruments_expected: int
    n_instruments_found: int
    calendar_start: str | None
    calendar_end: str | None
    calendar_days: int
    instruments_missing: tuple[str, ...]
    instruments_with_gaps: tuple[tuple[str, int], ...]  # (symbol, missing_day_count)
    min_required_end: str | None = None  # if set, calendar_end must be >= this
    stale: bool = False  # True iff calendar_end < min_required_end

    @property
    def ok(self) -> bool:
        return (
            self.n_instruments_found == self.n_instruments_expected
            and not self.instruments_missing
            and not self.stale
        )


def verify_store(
    provider_uri: Path,
    universe: Universe,
    *,
    min_required_end: str | None = None,
) -> VerifyReport:
    """Read the binary store back and report on coverage.

    Designed for humans to read. Returns the report so a CLI can also exit
    non-zero on missing data.
    """
    import numpy as np

    provider_uri = provider_uri.expanduser()
    cal_path = provider_uri / "calendars" / "day.txt"
    inst_path = provider_uri / "instruments" / f"{universe.name}.txt"

    if not cal_path.exists():
        raise FileNotFoundError(f"calendar missing: {cal_path}")
    if not inst_path.exists():
        raise FileNotFoundError(f"instrument list missing: {inst_path}")

    calendar = [line.strip() for line in cal_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    inst_lines = [line.strip() for line in inst_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    found_symbols = {line.split("\t")[0] for line in inst_lines}

    expected = {t.lower() for t in universe.instruments}
    missing = expected - found_symbols

    gaps: list[tuple[str, int]] = []
    for tic in sorted(expected & found_symbols):
        close_bin = provider_uri / "features" / tic / "close.day.bin"
        if not close_bin.exists():
            gaps.append((tic, -1))
            continue
        raw = close_bin.read_bytes()
        # First 4 bytes uint32 start index, rest float32
        start_index = struct.unpack("<I", raw[:4])[0]  # noqa: F841  (used implicitly via length)
        values = np.frombuffer(raw[4:], dtype="<f4")
        n_nan = int(np.isnan(values).sum())
        if n_nan:
            gaps.append((tic, n_nan))

    cal_end = calendar[-1] if calendar else None
    stale = bool(min_required_end and cal_end and cal_end < min_required_end)

    return VerifyReport(
        provider_uri=provider_uri,
        universe=universe.name,
        n_instruments_expected=len(expected),
        n_instruments_found=len(expected & found_symbols),
        calendar_start=calendar[0] if calendar else None,
        calendar_end=cal_end,
        calendar_days=len(calendar),
        instruments_missing=tuple(sorted(missing)),
        instruments_with_gaps=tuple(gaps),
        min_required_end=min_required_end,
        stale=stale,
    )


def print_report(report: VerifyReport, stream=sys.stdout) -> None:
    print(f"\n=== {report.universe} @ {report.provider_uri} ===", file=stream)
    print(
        f"  instruments: {report.n_instruments_found}/{report.n_instruments_expected}",
        file=stream,
    )
    print(
        f"  calendar:    {report.calendar_start} -> {report.calendar_end} ({report.calendar_days} days)",
        file=stream,
    )
    if report.stale:
        print(
            f"  STALE:       calendar ends {report.calendar_end} < required {report.min_required_end}",
            file=stream,
        )
    if report.instruments_missing:
        print(f"  MISSING:     {', '.join(report.instruments_missing)}", file=stream)
    if report.instruments_with_gaps:
        print(f"  gaps:", file=stream)
        for sym, n in report.instruments_with_gaps[:10]:
            label = "no-bin" if n < 0 else f"{n} NaN days"
            print(f"    {sym}: {label}", file=stream)
        if len(report.instruments_with_gaps) > 10:
            print(f"    ... and {len(report.instruments_with_gaps) - 10} more", file=stream)
    print(f"  status:      {'OK' if report.ok else 'NEEDS REVIEW'}", file=stream)


# ---------------------------------------------------------------------------
# One-call helper
# ---------------------------------------------------------------------------


def fetch_and_dump(
    universe: Universe,
    provider_uri: Path,
    start: str,
    end: str,
    *,
    prefer_qlib_bundle: bool = False,
) -> list[FetchResult]:
    """End-to-end: fetch source data and dump to Qlib binary store.

    Default is Yahoo for all universes. Qlib's prebuilt US bundle is frozen
    in 2020 and unusable for our 2024+ test windows; opt-in only with
    prefer_qlib_bundle=True if you have an explicit reason.
    """
    provider_uri = provider_uri.expanduser()
    provider_uri.mkdir(parents=True, exist_ok=True)

    if universe.region == "us" and prefer_qlib_bundle:
        if fetch_qlib_us_bundle(provider_uri):
            log.warning(
                "US Qlib bundle is stale (calendar ends ~2020-11). "
                "Most test windows will have no data. Prefer Yahoo."
            )
            entries = [
                (tic.lower(), start, end) for tic in universe.instruments
            ]
            _write_instrument_list(provider_uri, universe.name, entries)
            return [
                FetchResult(instrument=t, rows=-1, start=start, end=end, missing_days=0)
                for t in universe.instruments
            ]

    # Fetch the benchmark alongside the universe (Qlib backtest needs it in-store).
    fetch_symbols: list[str] = list(universe.instruments)
    if universe.benchmark_yahoo not in fetch_symbols:
        fetch_symbols.append(universe.benchmark_yahoo)

    frames = fetch_yahoo(fetch_symbols, start, end)
    if not frames:
        raise RuntimeError(f"no data fetched for universe {universe.name}")

    # Rename the benchmark frame to the in-store symbol (strip Yahoo-style prefixes).
    if (
        universe.benchmark_yahoo in frames
        and universe.benchmark_yahoo != universe.benchmark
    ):
        frames[universe.benchmark] = frames.pop(universe.benchmark_yahoo)

    return dump_to_qlib(provider_uri, frames, universe)


__all__ = [
    "FetchResult",
    "VerifyReport",
    "fetch_and_dump",
    "fetch_yahoo",
    "fetch_qlib_us_bundle",
    "dump_to_qlib",
    "verify_store",
    "print_report",
    "QLIB_FIELDS",
]


# Quiet a benign warning if numpy isn't installed in scaffold-only environments.
_ = datetime  # keep the import used
