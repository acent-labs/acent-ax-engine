"""Per-request analysis session driver.

Phase 1: each call to :meth:`AnalysisSession.stream` runs one full analysis
turn against a freshly spawned Hermes ACP transport. The driver:

1. Synthesizes the leading ``ACCEPTED`` event.
2. Boots the transport and sends the prompt derived from
   :class:`AXAnalysisRequest`.
3. Translates each ACP ``session/update`` into an :class:`AXStreamEvent`
   via :class:`StageTranslator` and yields it.
4. Synthesizes a terminal ``COMPLETED`` (with ``latency_ms`` and
   ``stage_history``) on a clean finish, or ``ERROR`` with one of the
   structured error codes below on failure:
     * ``HERMES_TIMEOUT`` — overall prompt budget exceeded.
     * ``HERMES_CRASHED`` — transport raised before reaching a clean end.

The driver is implemented as an async iterator so the FastAPI SSE route
can stream events directly to the gateway.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

from .config import BridgeSettings
from .contract import (
    AXAnalysisError,
    AXAnalysisRequest,
    AXStreamEvent,
    StreamStage,
)
from .hermes_client import HermesTransport
from .translate import StageTranslator

logger = logging.getLogger(__name__)


def build_prompt_text(request: AXAnalysisRequest, *, skill: str) -> str:
    """Render the prompt sent to Hermes.

    The bridge does not do prompt engineering — the actual orchestration
    lives in the ``acent-ax-analysis`` skill. We just hand the skill a
    structured request so it can validate and dispatch.
    """
    payload = request.model_dump(mode="json")
    return (
        f"/skill {skill}\n"
        "Run the AX ticket analysis pipeline against the following request.\n"
        "Treat ticket_data as the only source of truth — do not call Freshdesk.\n\n"
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
    )


class AnalysisSession:
    """One analysis run against one Hermes ACP subprocess."""

    def __init__(
        self,
        request: AXAnalysisRequest,
        transport: HermesTransport,
        settings: BridgeSettings,
    ) -> None:
        self._request = request
        self._transport = transport
        self._settings = settings
        self._translator = StageTranslator(request.job_id)

    async def stream(self) -> AsyncIterator[AXStreamEvent]:
        started_at = time.monotonic()

        yield AXStreamEvent(
            job_id=self._request.job_id,
            stage=StreamStage.ACCEPTED,
            message="job accepted",
            data={"correlation_id": self._request.correlation_id},
        )

        try:
            async for event in self._run_until_terminal():
                yield event
        except asyncio.TimeoutError:
            yield self._error_event(
                "HERMES_TIMEOUT",
                f"prompt exceeded {self._settings.prompt_timeout_s}s budget",
                should_retry=True,
            )
            return
        except Exception as exc:  # noqa: BLE001 — surface as structured error
            logger.exception("Hermes transport crashed during analysis")
            yield self._error_event(
                "HERMES_CRASHED",
                f"hermes transport crashed: {exc!s}",
                should_retry=False,
            )
            return
        finally:
            try:
                await self._transport.aclose()
            except Exception:  # noqa: BLE001
                logger.exception("Hermes transport aclose failed")

        latency_ms = int((time.monotonic() - started_at) * 1000)
        yield AXStreamEvent(
            job_id=self._request.job_id,
            stage=StreamStage.COMPLETED,
            message="analysis pipeline finished",
            data={
                "latency_ms": latency_ms,
                "stage_history": [s.value for s in self._translator.stage_history],
            },
        )

    async def _run_until_terminal(self) -> AsyncIterator[AXStreamEvent]:
        timeout = self._settings.prompt_timeout_s

        async def driver() -> AsyncIterator[AXStreamEvent]:
            await self._transport.start()
            prompt = build_prompt_text(self._request, skill=self._settings.hermes_skill)
            await self._transport.send_prompt(prompt)
            async for update in self._transport.session_updates():
                event = self._translator.translate(update)
                if event is not None:
                    yield event

        # Wrap the inner generator with a per-step timeout that sums to the
        # overall budget. Using a single asyncio.wait_for around the full
        # async-generator is not possible, so we drive it manually.
        agen = driver().__aiter__()
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                event = await asyncio.wait_for(agen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            yield event

    def _error_event(self, code: str, message: str, *, should_retry: bool) -> AXStreamEvent:
        info = AXAnalysisError(
            job_id=self._request.job_id,
            stage=self._translator.current_stage,
            error_code=code,
            message=message,
            should_retry=should_retry,
        )
        return AXStreamEvent(
            job_id=self._request.job_id,
            stage=StreamStage.ERROR,
            message=message,
            data=info.model_dump(mode="json"),
        )


__all__ = ["AnalysisSession", "build_prompt_text"]
