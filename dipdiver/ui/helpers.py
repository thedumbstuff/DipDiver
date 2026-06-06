"""Shared helpers for routes: scoreboard loader, run record loader, formatters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dipdiver._paths import repo_root
from dipdiver.harness.render import FusedDayRow, fuse_by_day
from dipdiver.harness.scoreboard import read_events


def load_fused_rows() -> list[FusedDayRow]:
    return fuse_by_day(read_events())


def load_run_record(date: str, universe: str) -> dict | None:
    """Read the raw m3_live JSON for a given (date, universe). Tries both the
    real and dryrun suffixes; real wins.
    """
    base = repo_root() / "logs" / "m3_live" / universe
    real = base / f"{date}.json"
    dryrun = base / f"{date}_dryrun.json"
    for p in (real, dryrun):
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


def fmt_currency(v: float | int | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.1%}"


def time_ago(ts: datetime | None) -> str:
    if ts is None:
        return "—"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - ts
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def pill_class_for_status(status: str | None) -> str:
    if status == "success":
        return "pill-ok"
    if status == "error":
        return "pill-err"
    if status == "running":
        return "pill-info"
    return "pill-muted"


def _current_path(request: Any) -> str:
    """Extract the current URL path for active-nav detection."""
    try:
        return str(request.url.path) if hasattr(request, "url") else "/"
    except Exception:  # noqa: BLE001
        return "/"


def nav_active(current_path: str, link_path: str) -> bool:
    """True if `link_path` should render as the active nav item.

    Matching rules:
      - exact match on "/" → only the root tab lights up on "/"
      - any other path is "active" if current_path starts with `link_path`
        followed by EOF, "/", or "?" — so /strategies/dow30 highlights
        Strategies but /strategies-compare does NOT (which is its own tab).
    """
    # Strip query string from current first — request.url.path won't have it
    # in production, but the helper is also unit-tested with literal strings.
    cp = current_path.split("?", 1)[0]
    if link_path == "/":
        return cp == "/"
    if cp == link_path:
        return True
    return cp.startswith(link_path + "/")


def template_ctx(request: Any, **extra: Any) -> dict:
    """Build the standard context every template needs (request, request global
    state like health). Merge `extra` overrides last.
    """
    current_path = _current_path(request)
    ctx: dict = {
        "request": request,
        "health_ok": True,
        "current_path": current_path,
        "nav_active": nav_active,
    }
    ctx.update(extra)
    return ctx
