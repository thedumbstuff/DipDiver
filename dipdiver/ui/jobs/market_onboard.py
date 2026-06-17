"""market_onboard — one-click market enablement.

Chains the four manual steps for enabling a registered universe (the M13
"adding a market" flow) into a single background job:

  1/4 fetch    — OHLCV → Qlib binary store. Skipped when the store already
                 covers the config's test window for every instrument.
  2/4 train    — run the M1 baseline, apply the same lock gates m1_retrain
                 uses, record a ModelVersion row, save the lock file.
  3/4 signals  — export data/signals/<config>.csv for the nightly runner.
  4/4 enable   — append the strategy entries to ui_config.yaml.

The job is parameterised (universe, model, committee variant) so it is NOT in
the cron registry; the /config "Add market" form starts it through
scheduler.run_adhoc(), which provides the per-job lock, the JobLog row, and
the `progress` callback that live-updates the status fragment.

Fetch safety: universes can share a Qlib provider dir (dow30 + sp500 both
live in us_data). dump_to_qlib() rewrites the store's calendar and all.txt
from whatever was fetched, so a partial fetch would desync the sibling
universe's feature bins from the new calendar. We therefore always fetch the
UNION of all universes mapped to the provider dir and regenerate every
sibling's instrument list afterwards.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime

from dipdiver._paths import repo_root
from dipdiver.ui import db
from dipdiver.ui.settings import (
    StrategyConfig,
    reload_ui_config,
    save_ui_config,
)

log = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

MODEL_KINDS = ("lightgbm", "lstm")

JOB_ID = "market_onboard"


def _ensure_repo_on_path() -> None:
    p = str(repo_root())
    if p not in sys.path:
        sys.path.insert(0, p)


def config_filename(universe_key: str, model_kind: str) -> str:
    return f"{universe_key}_{model_kind}.yaml"


def _config_path(universe_key: str, model_kind: str):
    return (
        repo_root()
        / "dipdiver"
        / "brain"
        / "baselines"
        / "configs"
        / config_filename(universe_key, model_kind)
    )


def _missing_brain_deps(model_kind: str) -> list[str]:
    """Importability check for the heavy stages.

    Returns a list of human-readable problems. A bare module name means it is
    not installed; "<mod> (OSError: …)" means the package is installed but
    fails to load — almost always a missing system library (e.g. lightgbm
    needs libgomp1). Distinguishing the two stops a native-lib gap from being
    reported as a missing pip package.
    """
    required = ["qlib", "yfinance", "pandas", "numpy"]
    required.append("torch" if model_kind == "lstm" else "lightgbm")
    missing = []
    for mod in required:
        try:
            __import__(mod)
        except ModuleNotFoundError:
            missing.append(mod)  # genuinely not installed
        except Exception as e:  # installed but won't import (native lib, ABI, …)
            missing.append(f"{mod} ({type(e).__name__}: {e})")
    return missing


def _fetch_stage(universe, baseline_cfg, progress: ProgressFn) -> str:
    """Make sure the Qlib store behind `baseline_cfg` covers this universe.

    Returns a one-line note for the final summary.
    """
    _ensure_repo_on_path()
    from dipdiver._paths import resolve_provider_uri
    from dipdiver.brain.baselines.data import (
        _write_instrument_list,
        dump_to_qlib,
        fetch_yahoo,
        verify_store,
    )
    from dipdiver.brain.baselines.universes import UNIVERSES
    from scripts.m1_setup import FETCH_WINDOWS, PROVIDER_DIR

    provider_uri = resolve_provider_uri(baseline_cfg.qlib_provider_uri)

    try:
        report = verify_store(provider_uri, universe, min_required_end=baseline_cfg.test_end)
        if report.ok:
            return "store already covers the test window — fetch skipped"
    except FileNotFoundError:
        pass  # no store yet — fetch below

    window = FETCH_WINDOWS.get(universe.name)
    if window is None:
        raise RuntimeError(
            f"no fetch window for universe {universe.name!r} in scripts/m1_setup.py "
            f"— add it to FETCH_WINDOWS/PROVIDER_DIR first"
        )
    start, end = window

    # Union fetch across every universe sharing this provider dir (see module
    # docstring). Benchmarks ride along — Qlib's backtest needs them in-store.
    dirname = PROVIDER_DIR.get(universe.name)
    siblings = [
        u
        for u in UNIVERSES.values()
        if u.name != universe.name and PROVIDER_DIR.get(u.name) == dirname
    ]
    symbols: list[str] = list(universe.instruments)
    benchmarks: dict[str, str] = {universe.benchmark_yahoo: universe.benchmark}
    for sib in siblings:
        symbols.extend(sib.instruments)
        benchmarks[sib.benchmark_yahoo] = sib.benchmark
    symbols = list(dict.fromkeys(symbols))
    for yahoo_sym in benchmarks:
        if yahoo_sym not in symbols:
            symbols.append(yahoo_sym)

    progress(
        f"1/4 fetching {len(symbols)} tickers ({start} → {end}) from Yahoo — "
        f"this can take a while…"
    )
    frames = fetch_yahoo(symbols, start, end)
    if not frames:
        raise RuntimeError(f"Yahoo returned no data for universe {universe.name}")
    for yahoo_sym, store_sym in benchmarks.items():
        if yahoo_sym in frames and yahoo_sym != store_sym:
            frames[store_sym] = frames.pop(yahoo_sym)

    dump_to_qlib(provider_uri, frames, universe)

    # dump_to_qlib wrote all.txt + <universe>.txt; regenerate the siblings'
    # instrument lists from the fresh all.txt so they stay calendar-consistent.
    all_txt = provider_uri / "instruments" / "all.txt"
    entries = [
        tuple(line.split("\t"))
        for line in all_txt.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for sib in siblings:
        members = {t.lower() for t in sib.instruments}
        _write_instrument_list(provider_uri, sib.name, [e for e in entries if e[0] in members])

    fetched = len(frames)
    if fetched < len(symbols):
        log.warning(
            "market_onboard fetch: %d/%d tickers fetched (Yahoo gaps possible)",
            fetched,
            len(symbols),
        )
    return f"fetched {fetched}/{len(symbols)} tickers"


def _train_stage(
    universe_key: str, model_kind: str, progress: ProgressFn
) -> tuple[dict, bool, str]:
    """Train the baseline, gate it, persist ModelVersion + lock file.

    Returns (metrics, gate_passed, gate_reason). Reuses m1_retrain's gates and
    ModelVersion writer so onboarded models follow the same rules as rolls.
    """
    from dipdiver.brain.baselines.config import load_config
    from dipdiver.brain.baselines.results import save_locked
    from dipdiver.brain.baselines.runner import run_baseline
    from dipdiver.ui.jobs.m1_retrain import _asset_class, _gate, _record_version

    cfg_name = config_filename(universe_key, model_kind)
    config = load_config(_config_path(universe_key, model_kind))
    result = run_baseline(config)
    metrics = {
        "sharpe": getattr(result, "sharpe", 0.0),
        "max_drawdown": getattr(result, "max_drawdown", 0.0),
        "hit_rate": getattr(result, "hit_rate", 0.0),
        "annualised_return": getattr(result, "annualised_return", 0.0),
        "psr": getattr(result, "psr", 0.0),
    }
    passed, reason = _gate(metrics, _asset_class(config.region))
    _record_version(
        config_name=cfg_name,
        config_hash=result.config_hash,
        train_start=config.train_start,
        train_end=config.train_end,
        test_start=config.test_start,
        test_end=config.test_end,
        metrics=metrics,
        status="locked" if passed else "candidate",
        notes=f"market_onboard: {reason}",
    )
    if passed:
        try:
            save_locked(result)
        except Exception as e:
            log.warning("market_onboard: save_locked skipped: %s", e)
    return metrics, passed, reason


def _signals_stage(universe_key: str, model_kind: str) -> int:
    _ensure_repo_on_path()
    from scripts.m3_export_signals import main as export_main

    try:
        return export_main(["--m1-config", config_filename(universe_key, model_kind)])
    except SystemExit as e:
        return int(e.code or 0)


def _enable_stage(universe_key: str, model_kind: str, add_committee_variant: bool) -> list[str]:
    """Append the new strategies to ui_config.yaml. Idempotent on strategy_id."""
    cfg = reload_ui_config()  # fresh from disk — don't clobber concurrent edits
    existing = {s.strategy_id for s in cfg.strategies}
    base_id = f"{universe_key}_{model_kind}"
    targets = [(base_id, False)]
    if add_committee_variant:
        targets.append((f"{base_id}_committee", True))

    added: list[str] = []
    for sid, with_committee in targets:
        if sid in existing:
            continue
        cfg.strategies.append(
            StrategyConfig(
                strategy_id=sid,
                m1_config=config_filename(universe_key, model_kind),
                with_committee=with_committee,
                enabled=True,
            )
        )
        added.append(sid)

    if added:
        cfg.last_modified_utc = datetime.now(UTC).isoformat(timespec="seconds")
        cfg.last_modified_by = "market_onboard"
        save_ui_config(cfg)
        reload_ui_config()
        with db.session() as s:
            s.add(
                db.ConfigAudit(
                    saved_utc=datetime.now(UTC),
                    actor="market_onboard",
                    diff_summary=f"added strategies: {', '.join(added)}",
                )
            )
    return added


def run_onboard(
    universe_key: str,
    model_kind: str = "lightgbm",
    add_committee_variant: bool = True,
    progress: ProgressFn = lambda _msg: None,
) -> dict:
    """Full onboarding pipeline. Returns the JobLog result dict (rc=0 on success)."""
    from dipdiver.brain.baselines.universes import UNIVERSES

    if universe_key not in UNIVERSES:
        return {"rc": 1, "error": f"unknown universe {universe_key!r}"}
    if model_kind not in MODEL_KINDS:
        return {"rc": 1, "error": f"unknown model {model_kind!r} (choose from {MODEL_KINDS})"}
    if not _config_path(universe_key, model_kind).exists():
        return {
            "rc": 1,
            "error": (
                f"no baseline config {config_filename(universe_key, model_kind)} — "
                f"add the YAML under dipdiver/brain/baselines/configs/ first"
            ),
        }
    missing = _missing_brain_deps(model_kind)
    if missing:
        return {
            "rc": 1,
            "error": (
                f"brain dependencies unavailable: {', '.join(missing)}. "
                f"A bare name means it is not installed (add the brain extra: pip "
                f'install -e ".[brain-lite]", or rebuild the Docker image). An '
                f"OSError means a missing system library — lightgbm needs libgomp1."
            ),
        }

    universe = UNIVERSES[universe_key]
    cfg_name = config_filename(universe_key, model_kind)

    from dipdiver.brain.baselines.config import load_config

    baseline_cfg = load_config(_config_path(universe_key, model_kind))

    progress(f"1/4 checking data store for {universe_key} ({len(universe)} tickers)…")
    fetch_note = _fetch_stage(universe, baseline_cfg, progress)

    progress(f"2/4 training {cfg_name} — minutes for lightgbm, longer for lstm…")
    metrics, passed, reason = _train_stage(universe_key, model_kind, progress)
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    if not passed:
        return {
            "rc": 1,
            "message": (
                f"{universe_key} trained but failed the lock gates ({reason}) — "
                f"strategy NOT enabled. Recorded as `candidate` on /models."
            ),
            "sharpe": sharpe,
            "fetch": fetch_note,
        }

    progress(f"3/4 exporting signals for {cfg_name}…")
    rc = _signals_stage(universe_key, model_kind)
    if rc != 0:
        return {
            "rc": rc,
            "error": f"signal export failed (rc={rc}) — model is locked; "
            f"re-run the signal_refresh job after fixing",
            "sharpe": sharpe,
        }

    progress("4/4 enabling strategies…")
    added = _enable_stage(universe_key, model_kind, add_committee_variant)

    note = (
        "" if universe.live_executable else " (research-only universe: signals + picks, no orders)"
    )
    return {
        "rc": 0,
        "message": (
            f"{universe_key} onboarded: sharpe={sharpe:.2f}, {fetch_note}; "
            f"enabled: {', '.join(added) if added else '(already configured)'}{note}"
        ),
        "universe": universe_key,
        "config": cfg_name,
        "sharpe": sharpe,
        "enabled": added,
        "live_executable": universe.live_executable,
    }
