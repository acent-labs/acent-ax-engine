"""FastAPI app factory for the ACENT AX bridge."""
from __future__ import annotations

from fastapi import FastAPI

from .config import BridgeSettings
from .hermes_client import TransportFactory, default_transport_factory
from .server.analyze import router as analyze_router
from .server.healthz import router as healthz_router


def create_app(
    *,
    settings: BridgeSettings | None = None,
    transport_factory: TransportFactory | None = None,
) -> FastAPI:
    """Build a FastAPI app for the AX bridge.

    Both ``settings`` and ``transport_factory`` are injectable so unit tests
    can swap a fake ``HermesTransport`` in without spawning a subprocess.
    """
    app = FastAPI(title="ACENT AX Bridge", version="0.1.0")
    app.state.bridge_settings = settings if settings is not None else BridgeSettings()
    app.state.transport_factory = transport_factory or default_transport_factory
    app.include_router(healthz_router)
    app.include_router(analyze_router)
    return app
