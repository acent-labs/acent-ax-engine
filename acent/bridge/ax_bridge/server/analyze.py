"""POST /v1/analyze SSE entry."""
from __future__ import annotations

import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from ..config import BridgeSettings
from ..contract import AXAnalysisRequest, AXStreamEvent
from ..hermes_client import TransportFactory, default_transport_factory
from ..session import AnalysisSession

logger = logging.getLogger(__name__)

router = APIRouter()


def _settings_dep(request: Request) -> BridgeSettings:
    settings = getattr(request.app.state, "bridge_settings", None)
    if settings is None:
        raise HTTPException(status_code=500, detail="bridge_settings not configured")
    return settings


def _transport_factory_dep(request: Request) -> TransportFactory:
    factory = getattr(request.app.state, "transport_factory", None)
    return factory or default_transport_factory


def _check_bearer(authorization: str | None, settings: BridgeSettings) -> None:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authorization header",
        )
    if token != settings.ax_engine_internal_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )


def _format_sse(event: AXStreamEvent) -> bytes:
    payload = event.model_dump(mode="json")
    body = json.dumps(payload, ensure_ascii=False)
    return f"event: {event.stage.value}\ndata: {body}\n\n".encode("utf-8")


@router.post("/v1/analyze")
async def analyze(
    request: AXAnalysisRequest,
    authorization: str | None = Header(default=None),
    settings: BridgeSettings = Depends(_settings_dep),
    transport_factory: TransportFactory = Depends(_transport_factory_dep),
) -> StreamingResponse:
    _check_bearer(authorization, settings)
    transport = transport_factory(settings)
    session = AnalysisSession(request=request, transport=transport, settings=settings)

    async def event_stream() -> AsyncIterator[bytes]:
        async for event in session.stream():
            yield _format_sse(event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
