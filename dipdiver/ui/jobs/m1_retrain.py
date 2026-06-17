"""m1_retrain — roll the M1 config windows and re-run the Qlib pipeline.

For each enabled strategy, load the m1_config YAML, call `roll_window()` to
shift train/valid/test forward, and execute the baseline pipeline. The
resulting metrics + lock status are recorded in `ModelVersion`.

The actual Qlib invocation is the heavy part — it's lazy-imported so this
module can be imported (and the scheduler can register it) even without the
brain extras installed. In environments without Qlib (e.g. CI / smoke tests),
the job records a `rejected` ModelVersion row with reason="qlib_unavailable"
and returns rc=0 — surfacing the gap without breaking the scheduler.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from dipdiver._paths import repo_root
from dipdiver.ui import db
from dipdiver.ui.settings import ui_config

log = logging.getLogger(__name__)


# Per-asset-class lock gates. A rolled/onboarded model that fails these stays
# `candidate`; the locked model keeps serving picks until a passing roll lands
# (a failing retrain never supersedes a good lock — see _record_version).
#
# `psr_min` is the Probabilistic Sharpe Ratio bar: confidence that the TRUE
# Sharpe exceeds 0 given the sample length and the return distribution's
# skew/kurtosis. 0.95 is the textbook bar (Bailey & López de Prado) and was
# validated to pass a genuinely-good model (dow30 PSR≈0.97, Sharpe 1.28) while
# correctly rejecting a marginal one (3-name crypto PSR≈0.67). It stops a
# lucky/short-sample Sharpe from locking — which a raw point estimate hides.
_LOCK_GATES: dict[str, dict[str, float]] = {
    "default": {"sharpe_min": 0.5, "max_dd_max": 0.30, "hit_rate_min": 0.45, "psr_min": 0.95},
    # Crypto is higher-vol and trades 24/7; a wider drawdown band is realistic.
    # We keep the same statistical-confidence bar so breadth/edge — not luck —
    # is what locks it.
    "crypto": {"sharpe_min": 0.5, "max_dd_max": 0.40, "hit_rate_min": 0.45, "psr_min": 0.95},
}


def _asset_class(region: str | None) -> str:
    """Map a config/universe region to a _LOCK_GATES key."""
    return "crypto" if (region or "").lower() == "crypto" else "default"


# Buffer in trading days subtracted from Qlib's last available date when
# capping the rolled test_end. Qlib's backtest engine reads `calendar[i+1]`
# for the next step, so we need at least one day of headroom past the
# nominal test_end. Two days gives us a weekend cushion.
_CALENDAR_HEADROOM_DAYS = 2


def _safe_anchor_date(config) -> str | None:
    """Return a YYYY-MM-DD anchor that won't run the backtest off the end
    of Qlib's calendar.

    Returns today UTC capped to (qlib_calendar_last - HEADROOM). When the
    Qlib data store can't be queried at all (corrupt, missing, region wrong),
    returns None and the caller can fall back to today UTC and let the run
    surface a clearer error.
    """
    try:
        from qlib.constant import REG_CN, REG_US
        from qlib.data import D
        from dipdiver._paths import resolve_provider_uri
        from dipdiver.brain.baselines._qlib.init import safe_qlib_init

        region = (
            REG_CN if (config.region or "").lower() == "cn" else REG_US
        )
        safe_qlib_init(
            provider_uri=str(resolve_provider_uri(config.qlib_provider_uri)),
            region=region,
        )
        cal = D.calendar(freq="day")
        if cal is None or len(cal) == 0:
            return None
        last = cal[-1]
        # `cal[-1]` is a Timestamp / Datetime depending on version.
        last_date = last.date() if hasattr(last, "date") else last
        safe = last_date - timedelta(days=_CALENDAR_HEADROOM_DAYS)
        today = datetime.now(timezone.utc).date()
        anchor = min(today, safe)
        return anchor.isoformat()
    except Exception as e:  # noqa: BLE001
        log.warning("could not determine Qlib calendar end: %s", e)
        return None


def _configs_dir():
    return repo_root() / "dipdiver" / "brain" / "baselines" / "configs"


def _resolve_config_path(filename: str):
    return _configs_dir() / filename


def _record_version(
    *,
    config_name: str,
    config_hash: str,
    train_start: str, train_end: str,
    test_start: str, test_end: str,
    metrics: dict,
    status: str,
    notes: str = "",
) -> None:
    """Insert a ModelVersion row. `metrics` is a plain dict — callers extract
    the headline fields from a BaselineResult before passing them here.
    """
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    max_dd = float(metrics.get("max_drawdown", 0.0) or 0.0)
    hit_rate = float(metrics.get("hit_rate", 0.0) or 0.0)
    psr = float(metrics.get("psr", 0.0) or 0.0)
    with db.session() as s:
        # Mark the previous locked row as superseded if this one is locked.
        if status == "locked":
            prev = (
                s.query(db.ModelVersion)
                .filter(db.ModelVersion.config_name == config_name)
                .filter(db.ModelVersion.status == "locked")
                .all()
            )
            for r in prev:
                r.status = "superseded"
        s.add(db.ModelVersion(
            config_name=config_name,
            config_hash=config_hash,
            locked_on_utc=datetime.now(timezone.utc),
            train_start=train_start, train_end=train_end,
            test_start=test_start, test_end=test_end,
            sharpe=sharpe, max_dd=max_dd, hit_rate=hit_rate, psr=psr,
            status=status, notes=notes,
        ))


def _gate(metrics: dict, asset_class: str = "default") -> tuple[bool, str]:
    gates = _LOCK_GATES.get(asset_class, _LOCK_GATES["default"])
    sharpe = float(metrics.get("sharpe", 0.0) or 0.0)
    mdd = abs(float(metrics.get("max_drawdown", 0.0) or 0.0))
    hit = float(metrics.get("hit_rate", 0.0) or 0.0)
    psr = float(metrics.get("psr", 0.0) or 0.0)
    if sharpe < gates["sharpe_min"]:
        return False, f"sharpe {sharpe:.2f} < {gates['sharpe_min']}"
    if mdd > gates["max_dd_max"]:
        return False, f"max_dd {mdd:.2f} > {gates['max_dd_max']}"
    if hit < gates["hit_rate_min"]:
        return False, f"hit_rate {hit:.2f} < {gates['hit_rate_min']}"
    if psr < gates["psr_min"]:
        return False, (
            f"psr {psr:.2f} < {gates['psr_min']} "
            f"(Sharpe not statistically significant for the sample length)"
        )
    return True, "all gates passed"


def run() -> dict:
    cfg = ui_config()
    seen_configs: set[str] = set()
    results: list[dict] = []
    overall_rc = 0

    for strat in cfg.strategies:
        if not strat.enabled or strat.m1_config in seen_configs:
            continue
        seen_configs.add(strat.m1_config)
        config_path = _resolve_config_path(strat.m1_config)
        if not config_path.exists():
            log.warning("m1_retrain: %s missing", strat.m1_config)
            results.append({"config": strat.m1_config, "status": "missing"})
            overall_rc = 1
            continue
        try:
            from dipdiver.brain.baselines.config import load_config
        except Exception as e:  # noqa: BLE001
            results.append({
                "config": strat.m1_config, "status": "skipped",
                "reason": f"load_config import failed: {e}",
            })
            continue
        base = load_config(config_path)
        try:
            # Cap the rolled test_end to Qlib's last available date (minus a
            # short buffer for `calendar[i+1]` lookups in the backtest engine).
            # Without this, rolling to "today" runs the backtest off the end
            # of the local data store and Qlib raises IndexError.
            anchor = _safe_anchor_date(base)
            log.info(
                "m1_retrain: anchor for %s = %s (today UTC capped by qlib calendar)",
                strat.m1_config, anchor or "today",
            )
            rolled = base.roll_window(cadence="1y", anchor_date=anchor)
        except Exception as e:  # noqa: BLE001
            log.warning("m1_retrain: roll_window failed for %s: %s", strat.m1_config, e)
            results.append({"config": strat.m1_config, "status": "skipped", "reason": str(e)})
            continue

        # Lazy-import the runner. Distinguish a genuinely missing Qlib install
        # (ModuleNotFoundError → "qlib_unavailable") from other ImportErrors
        # (e.g. typo on a name we tried to import → surfaced verbatim).
        try:
            from dipdiver.brain.baselines.runner import run_baseline
        except ModuleNotFoundError as e:
            log.warning("m1_retrain: required module missing (%s)", e)
            reason = f"module_missing: {e.name or e}"
            _record_version(
                config_name=strat.m1_config,
                config_hash=rolled.config_hash,
                train_start=rolled.train_start, train_end=rolled.train_end,
                test_start=rolled.test_start, test_end=rolled.test_end,
                metrics={}, status="rejected",
                notes=reason,
            )
            results.append({
                "config": strat.m1_config, "status": "rejected",
                "reason": reason,
            })
            continue
        except Exception as e:  # noqa: BLE001
            # Real ImportError (bad attribute name etc.) — must NOT be hidden
            # behind "qlib_unavailable". Show class + message.
            log.exception("m1_retrain: import failed for %s", strat.m1_config)
            reason = f"import_error: {type(e).__name__}: {e}"
            _record_version(
                config_name=strat.m1_config,
                config_hash=rolled.config_hash,
                train_start=rolled.train_start, train_end=rolled.train_end,
                test_start=rolled.test_start, test_end=rolled.test_end,
                metrics={}, status="rejected",
                notes=reason,
            )
            results.append({
                "config": strat.m1_config, "status": "rejected",
                "reason": reason,
            })
            overall_rc = 1
            continue

        try:
            outcome = run_baseline(rolled)
            # BaselineResult is a frozen dataclass — its fields ARE the
            # metrics (no `.metrics` attribute, no `.get()`). Extract by name.
            metrics = {
                "sharpe": getattr(outcome, "sharpe", 0.0),
                "max_drawdown": getattr(outcome, "max_drawdown", 0.0),
                "hit_rate": getattr(outcome, "hit_rate", 0.0),
                "annualised_return": getattr(outcome, "annualised_return", 0.0),
                "annualised_volatility": getattr(outcome, "annualised_volatility", 0.0),
                "n_trades": getattr(outcome, "n_trades", 0),
                "psr": getattr(outcome, "psr", 0.0),
            }
        except IndexError as e:
            # Qlib's backtest engine walks `calendar[i+1]` per step. If we get
            # an IndexError here, the rolled test_end is past the data store's
            # last available date — usually means the local Qlib snapshot
            # hasn't been refreshed.
            log.warning(
                "m1_retrain: calendar overrun for %s "
                "(test_end=%s probably past data store)",
                strat.m1_config, rolled.test_end,
            )
            _record_version(
                config_name=strat.m1_config,
                config_hash=rolled.config_hash,
                train_start=rolled.train_start, train_end=rolled.train_end,
                test_start=rolled.test_start, test_end=rolled.test_end,
                metrics={}, status="rejected",
                notes=(
                    f"calendar_overrun: test_end {rolled.test_end} is past "
                    f"the Qlib data store's last date. Refresh the snapshot "
                    f"(python scripts/m1_setup.py) and re-trigger m1_retrain."
                ),
            )
            results.append({
                "config": strat.m1_config, "status": "rejected",
                "reason": "calendar_overrun — refresh Qlib data snapshot",
            })
            overall_rc = 1
            continue
        except Exception as e:  # noqa: BLE001
            log.exception("m1_retrain failed for %s", strat.m1_config)
            _record_version(
                config_name=strat.m1_config,
                config_hash=rolled.config_hash,
                train_start=rolled.train_start, train_end=rolled.train_end,
                test_start=rolled.test_start, test_end=rolled.test_end,
                metrics={}, status="rejected",
                notes=f"training error: {type(e).__name__}: {e}",
            )
            results.append({
                "config": strat.m1_config, "status": "rejected",
                "reason": f"{type(e).__name__}: {e}",
            })
            overall_rc = 1
            continue

        passed, reason = _gate(metrics, _asset_class(base.region))
        _record_version(
            config_name=strat.m1_config,
            config_hash=rolled.config_hash,
            train_start=rolled.train_start, train_end=rolled.train_end,
            test_start=rolled.test_start, test_end=rolled.test_end,
            metrics=metrics,
            status="locked" if passed else "candidate",
            notes=reason,
        )
        results.append({
            "config": strat.m1_config,
            "status": "locked" if passed else "candidate",
            "sharpe": metrics.get("sharpe"),
            "reason": reason,
        })

        # Auto-refresh signals after a successful lock.
        if passed:
            try:
                import sys
                p = str(repo_root())
                if p not in sys.path:
                    sys.path.insert(0, p)
                from scripts.m3_export_signals import main as export_main
                export_main(["--m1-config", strat.m1_config])
            except Exception as e:  # noqa: BLE001
                log.warning("signal refresh after lock failed: %s", e)

    return {
        "rc": overall_rc,
        "configs_processed": len(results),
        "results": results,
        "message": json.dumps(results)[:240],
    }
