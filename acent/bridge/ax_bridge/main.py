"""FastAPI app entry for the ACENT AX bridge."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from ax_bridge import __version__
from ax_bridge.config import get_settings
from ax_bridge.server.analyze import router as analyze_router
from ax_bridge.server.healthz import router as healthz_router


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(
        title="acent-ax-bridge",
        version=__version__,
        description="FastAPI ↔ Hermes ACP bridge for ACENT AX engine",
    )
    app.include_router(healthz_router)
    app.include_router(analyze_router)
    return app


app = create_app()
