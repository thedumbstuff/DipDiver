"""Unit tests for config-driven labels (Phase H) and walk-forward eval (Phase G)."""

from __future__ import annotations

from dipdiver.brain.baselines.config import BaselineConfig
from dipdiver.brain.baselines.results import BaselineResult


def _cfg(**over) -> BaselineConfig:
    base = {
        "name": "t", "universe": "dow30", "model": "lightgbm",
        "train_start": "2016-01-01", "train_end": "2021-12-31",
        "valid_start": "2022-01-01", "valid_end": "2022-12-31",
        "test_start": "2023-01-01", "test_end": "2024-12-31",
        "benchmark": "DJI", "qlib_provider_uri": "x", "region": "us", "seed": 42,
    }
    base.update(over)
    return BaselineConfig(**base)


# --- Phase H: config-driven label -----------------------------------------

def test_label_default_matches_original_expression():
    from dipdiver.brain.baselines._qlib.task import _label_exprs

    # h=1 (default) must reproduce the original next-period forward return.
    assert _label_exprs(_cfg()) == ["Ref($close, -2) / Ref($close, -1) - 1"]


def test_label_horizon_extends_forward_window():
    from dipdiver.brain.baselines._qlib.task import _label_exprs

    assert _label_exprs(_cfg(label_horizon=5)) == ["Ref($close, -6) / Ref($close, -1) - 1"]


def test_label_vol_normalize_divides_by_trailing_vol():
    from dipdiver.brain.baselines._qlib.task import _label_exprs

    expr = _label_exprs(_cfg(label_horizon=5, label_vol_normalize=True))[0]
    assert "Std($close / Ref($close, 1) - 1" in expr  # trailing (past-only) vol
    assert expr.startswith("(Ref($close, -6) / Ref($close, -1) - 1)")


def test_label_fields_enter_config_hash():
    assert _cfg().config_hash != _cfg(label_horizon=5).config_hash


# --- Phase G: walk-forward -------------------------------------------------

def _result(sharpe, psr, test_end="2024-12-31") -> BaselineResult:
    return BaselineResult(
        config_hash="h", config_name="t", universe="dow30", model="lightgbm",
        test_start="2023-01-01", test_end=test_end,
        annualised_return=0.1, annualised_volatility=0.1, sharpe=sharpe,
        max_drawdown=-0.1, hit_rate=0.5, turnover=1.0, n_trades=10,
        benchmark_annualised_return=0.05, excess_return=0.05,
        qlib_version="x", git_sha="x", run_timestamp_utc="x", psr=psr,
    )


def test_run_walkforward_steps_anchor_back_per_fold(monkeypatch):
    import dipdiver.brain.baselines.runner as runner

    seen_test_ends: list[str] = []

    def fake_run(cfg):
        seen_test_ends.append(cfg.test_end)
        return _result(1.0, 0.96, test_end=cfg.test_end)

    monkeypatch.setattr(runner, "run_baseline", fake_run)
    results = runner.run_walkforward(_cfg(), n_folds=3, step_days=365)
    assert len(results) == 3
    # fold 0 anchors at the config's own test_end; each later fold steps back
    # exactly step_days (calendar arithmetic, so account for leap years).
    from datetime import date, timedelta

    base = date(2024, 12, 31)
    expected = [(base - timedelta(days=365 * k)).isoformat() for k in range(3)]
    assert seen_test_ends == expected
    # strictly walking backward in time
    assert seen_test_ends[0] > seen_test_ends[1] > seen_test_ends[2]


def test_walkforward_summary_aggregates_distribution():
    from dipdiver.brain.baselines.runner import walkforward_summary

    res = [_result(1.2, 0.97), _result(0.4, 0.80), _result(0.9, 0.96)]
    s = walkforward_summary(res, sharpe_min=0.5, psr_min=0.95)
    assert s["n_folds"] == 3
    assert s["sharpe_median"] == 0.9
    assert s["sharpe_min"] == 0.4
    # only folds with sharpe>=0.5 AND psr>=0.95 pass: (1.2,0.97) and (0.9,0.96) → 2/3
    assert abs(s["frac_passing"] - 2 / 3) < 1e-9


def test_walkforward_summary_empty_is_safe():
    from dipdiver.brain.baselines.runner import walkforward_summary

    s = walkforward_summary([])
    assert s["n_folds"] == 0 and s["frac_passing"] == 0.0
