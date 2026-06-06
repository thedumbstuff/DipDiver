"""FastAPI app factory.

Local dev:
    dipdiver-ui serve
    → http://127.0.0.1:8765

Container:
    DIPDIVER_UI_HOST=0.0.0.0 DIPDIVER_UI_PORT=8765 dipdiver-ui serve
    → bind to all interfaces; Caddy/Tailscale handle ingress.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dipdiver._paths import repo_root
from dipdiver.ui import db, env_loader
from dipdiver.ui.jobs import scheduler


log = logging.getLogger(__name__)


# Some jobs import from the top-level `scripts/` directory (e.g.
# `from scripts.m3_export_signals import main`). When `dipdiver-ui` is invoked
# as a console_scripts entry, sys.path does NOT include the repo root by
# default — so those imports fail with "No module named 'scripts.…'". Inject
# the repo root once at module import so every later import resolves cleanly.
_REPO = repo_root()
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))


# Load .env.m2 (and .env.m2.example as a local-dev fallback) into os.environ
# BEFORE any route or scheduler job tries to read credentials. Production VMs
# that set env vars in the systemd unit are unaffected — env_loader never
# overwrites an already-set value.
env_loader.load_env_files()


_THIS_DIR = Path(__file__).parent
_TEMPLATE_DIR = _THIS_DIR / "templates"
_STATIC_DIR = _THIS_DIR / "static"


templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@asynccontextmanager
async def _lifespan(app: FastAPI):
    db.init_db()
    scheduler.register_all()
    log.info("dipdiver-ui ready")
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="DipDiver Ops",
        description="Operator console for DipDiver. See docs/milestones/M8_ops_ui.md.",
        version="0.1.0",
        lifespan=_lifespan,
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Import + mount routers
    from dipdiver.ui.routes import (
        config_page,
        dashboard,
        decisions,
        health,
        logs,
        models_page,
        persona_accuracy,
        picks,
        positions,
        registry_api,
        runs,
        schedule_page,
        scoreboard_page,
        strategies,
        triggers,
    )

    app.include_router(dashboard.router)
    app.include_router(picks.router)
    app.include_router(strategies.router)
    app.include_router(runs.router)
    app.include_router(decisions.router)
    app.include_router(positions.router)
    app.include_router(scoreboard_page.router)
    app.include_router(triggers.router)
    app.include_router(config_page.router)
    app.include_router(schedule_page.router)
    app.include_router(health.router)
    app.include_router(logs.router)
    app.include_router(registry_api.router)
    app.include_router(models_page.router)
    app.include_router(persona_accuracy.router)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        # Tiny inline SVG so browsers don't 404 on every page
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
            '<rect width="16" height="16" rx="3" fill="#1f6f3a"/>'
            '<text x="8" y="12" font-size="10" font-family="monospace" '
            'fill="#fff" text-anchor="middle">DD</text></svg>'
        )
        return HTMLResponse(svg, media_type="image/svg+xml")

    return app


app = create_app()
