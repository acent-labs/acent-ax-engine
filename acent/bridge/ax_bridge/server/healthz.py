"""Liveness probe for the bridge."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from ax_bridge import __version__

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "service": "acent-ax-bridge",
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
