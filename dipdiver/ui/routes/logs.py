"""Logs page — tail recent files under logs/."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from dipdiver._paths import ui_logs_dir
from dipdiver.ui.helpers import template_ctx


router = APIRouter()


def _discover_logs(base: Path) -> list[dict]:
    """List files under logs/ recursively, sorted by mtime desc."""
    if not base.exists():
        return []
    out: list[dict] = []
    for p in base.rglob("*"):
        if p.is_file():
            try:
                stat = p.stat()
            except OSError:
                continue
            out.append({
                "rel": str(p.relative_to(base)).replace("\\", "/"),
                "size_kb": stat.st_size // 1024,
                "mtime": stat.st_mtime,
            })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:200]


@router.get("/logs", response_class=HTMLResponse)
async def logs_index(request: Request):
    from dipdiver.ui.app import templates

    base = ui_logs_dir()
    files = _discover_logs(base)
    ctx = template_ctx(request, files=files, base=str(base))
    return templates.TemplateResponse(request, "logs.html", ctx)


@router.get("/logs/{path:path}", response_class=PlainTextResponse)
async def logs_tail(path: str, lines: int = 500):
    """Return the last N lines of one log file. Path-traversal guarded."""
    base = ui_logs_dir().resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        return PlainTextResponse("forbidden", status_code=403)
    if not target.exists() or not target.is_file():
        return PlainTextResponse("not found", status_code=404)
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return PlainTextResponse(f"read error: {e}", status_code=500)
    tail = "\n".join(text.splitlines()[-lines:])
    return tail
