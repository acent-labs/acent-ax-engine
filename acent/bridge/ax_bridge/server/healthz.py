"""Unauthenticated liveness probe."""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    return {
        "status": "ok",
        "service": "acent-ax-bridge",
        "version": __version__,
    }
