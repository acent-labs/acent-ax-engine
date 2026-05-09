"""POST /v1/analyze — bridge HTTP/SSE entry."""

from __future__ import annotations

import logging
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from ax_bridge.config import Settings, get_settings
from ax_bridge.contract import AXAnalysisRequest, AXStreamEvent
from ax_bridge.session import stream_analysis

logger = logging.getLogger(__name__)

router = APIRouter(tags=["analyze"])


def _require_internal_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = (settings.ax_engine_internal_token or "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    presented = authorization[len("Bearer "):].strip()
    if presented != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )


def _format_sse(event: AXStreamEvent) -> str:
    return f"data: {event.model_dump_json()}\n\n"


async def _run(request: AXAnalysisRequest, settings: Settings) -> AsyncGenerator[str, None]:
    logger.info(
        "[bridge] analyze tenant=%s ticket=%s trigger=%s",
        request.tenant_id,
        request.external_ticket_id,
        request.trigger_source.value,
    )
    async for event in stream_analysis(request, settings):
        yield _format_sse(event)


@router.post("/v1/analyze", dependencies=[Depends(_require_internal_token)])
async def analyze(
    request: AXAnalysisRequest,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    return StreamingResponse(
        _run(request, settings),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
