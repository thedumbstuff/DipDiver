"""Telegram alerts. Optional — only fires if bot_token + chat_id are set.

If Telegram is unconfigured, send_alert() logs and returns silently.
"""

from __future__ import annotations

import logging

import httpx

from dipdiver.ui.settings import env_settings, ui_config


log = logging.getLogger(__name__)


def _telegram_target() -> tuple[str, str] | None:
    env = env_settings()
    token = env.telegram_bot_token
    chat_id = ui_config().telegram_chat_id or env.telegram_chat_id
    if not token or not chat_id:
        return None
    return token, chat_id


def send_alert(message: str, *, severity: str = "info") -> bool:
    """Push a Telegram message. Returns True on success, False on failure or
    if Telegram is unconfigured.
    """
    target = _telegram_target()
    if target is None:
        log.debug("alert (telegram unconfigured): %s", message)
        return False
    token, chat_id = target
    prefix = {
        "info": "ℹ️",
        "warn": "⚠️",
        "error": "🚨",
    }.get(severity, "ℹ️")
    body = f"{prefix} *DipDiver* — {message}"
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": body, "parse_mode": "Markdown"},
            timeout=10.0,
        )
        if r.status_code != 200:
            log.warning("telegram send failed: %s %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("telegram exception: %s", e)
        return False
