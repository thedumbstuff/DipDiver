"""Lock-gate behaviour: per-asset-class bars + the PSR bar scaling with whether
the universe trades live capital (research-only gets a looser 0.90 vs 0.95)."""

from __future__ import annotations

from dipdiver.ui.jobs.m1_retrain import _gate


def _m(**over):
    base = {"sharpe": 1.0, "max_drawdown": -0.10, "hit_rate": 0.55, "psr": 0.99}
    base.update(over)
    return base


def test_psr_bar_is_stricter_for_live_than_research():
    # PSR sits between the two bars (0.90 research, 0.95 live).
    m = _m(psr=0.92)
    assert _gate(m, "default", live_executable=True)[0] is False   # 0.92 < 0.95
    assert _gate(m, "default", live_executable=False)[0] is True   # 0.92 >= 0.90


def test_low_psr_fails_both_tiers():
    m = _m(psr=0.80)
    assert _gate(m, "default", live_executable=True)[0] is False
    assert _gate(m, "default", live_executable=False)[0] is False


def test_research_crypto_passes_just_under_live_bar():
    # The deployed case: crypto/nifty ~0.94/0.90 PSR, research-only -> lock.
    assert _gate(_m(psr=0.94), "crypto", live_executable=False)[0] is True


def test_crypto_uses_wider_drawdown_band():
    # MDD 0.35: within crypto's 0.40 band, over the default 0.30.
    m = _m(max_drawdown=-0.35)
    assert _gate(m, "crypto", live_executable=False)[0] is True
    assert _gate(m, "default", live_executable=False)[0] is False


def test_default_live_is_the_unchanged_strict_path():
    # An equity-grade model still locks under the strict live bar.
    assert _gate(_m(sharpe=1.28, max_drawdown=-0.18, hit_rate=0.57, psr=0.97))[0] is True
