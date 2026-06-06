"""UI settings: env-driven config + ui_config.yaml overlay.

Three sources, highest priority first:
  1. Environment variables (DIPDIVER_UI_*)
  2. ui_config.yaml (operator-edited via /config page)
  3. Defaults in this module

The YAML is the place operators change config from the UI. Env vars are for
deploy-time wiring (data root, bind address, telegram tokens).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from dipdiver._paths import ui_config_path


# ---------------------------------------------------------------------------
# Env-driven (immutable at runtime)
# ---------------------------------------------------------------------------


class EnvSettings(BaseSettings):
    """Values that come from env vars or .env files. Never edited via UI."""

    model_config = SettingsConfigDict(
        env_prefix="DIPDIVER_UI_",
        env_file=(".env.ui", ".env.m2"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    host: str = "127.0.0.1"  # bind address; override to 0.0.0.0 in container
    port: int = 8765
    reload: bool = False
    log_level: str = "INFO"

    # Telegram alerts (optional)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Used by deploy/Caddyfile to surface the canonical URL in the UI
    public_url: str | None = None


# ---------------------------------------------------------------------------
# YAML-driven (mutable via UI /config page)
# ---------------------------------------------------------------------------


class StrategyConfig(BaseModel):
    """One strategy the scheduler runs nightly."""

    strategy_id: str  # e.g. "dow30_lightgbm_committee"
    m1_config: str  # e.g. "dow30_lightgbm.yaml"
    with_committee: bool = False
    enabled: bool = True
    # QW10 — bound LLM cost per strategy-day. None = unlimited (current behavior).
    # When set, the committee halts further symbol reviews once daily cost meets
    # this ceiling; remaining buys default to approved with an "auto-approved
    # (cost ceiling reached)" annotation.
    committee_cost_daily_ceiling_usd: float | None = None
    # Stage 5 (M12) — penalty applied to a symbol's score when the operator has
    # recently thumbs-downed it. 1.0 = no penalty. 0.85 = -15% per veto.
    feedback_rank_penalty: float = 0.85
    # M14 — holding window used by veto_backfill for THIS strategy's vetoes.
    veto_regret_window_days: int = 5
    # M14 — whether sells route through the committee (default: false, sells
    # skip review to preserve risk-reducing exits).
    review_sells: bool = False


class UiConfig(BaseModel):
    strategies: list[StrategyConfig] = Field(default_factory=list)
    timezone: str = "UTC"  # cron expressions are interpreted in this TZ
    # Telegram chat ID can also live here (overrides env var). Useful when
    # someone wants to change recipient without restarting the container.
    telegram_chat_id: str | None = None
    # Stage 2 (M9) — risk band drives /picks position sizing.
    # aggressive: 5% per pick; balanced: 3%; conservative: 1%.
    risk_band: str = "balanced"
    # Stage 5 (M12) — how many days of feedback influence rank.
    feedback_lookback_days: int = 30
    # Tracks the last operator who hit "save config" — for an audit-light trail
    last_modified_utc: str | None = None
    last_modified_by: str | None = None  # placeholder for future multi-user

    @classmethod
    def default(cls) -> "UiConfig":
        return cls(
            strategies=[
                StrategyConfig(
                    strategy_id="dow30_lightgbm",
                    m1_config="dow30_lightgbm.yaml",
                    with_committee=False,
                    enabled=True,
                ),
                StrategyConfig(
                    strategy_id="dow30_lightgbm_committee",
                    m1_config="dow30_lightgbm.yaml",
                    with_committee=True,
                    enabled=True,
                ),
            ],
        )


def load_ui_config(path: Path | None = None) -> UiConfig:
    p = path or ui_config_path()
    if not p.exists():
        return UiConfig.default()
    raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return UiConfig.model_validate(raw)


def save_ui_config(cfg: UiConfig, path: Path | None = None) -> None:
    p = path or ui_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Module-level accessors
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def env_settings() -> EnvSettings:
    return EnvSettings()


# Live config (not lru_cached — re-read after edits)
_ui_config_cache: UiConfig | None = None


def ui_config() -> UiConfig:
    global _ui_config_cache
    if _ui_config_cache is None:
        _ui_config_cache = load_ui_config()
    return _ui_config_cache


def reload_ui_config() -> UiConfig:
    """Force re-read of ui_config.yaml after an edit."""
    global _ui_config_cache
    _ui_config_cache = load_ui_config()
    return _ui_config_cache
