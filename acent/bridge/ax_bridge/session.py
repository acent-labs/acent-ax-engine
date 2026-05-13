"""Per-request analysis session driver.

Phase 1: each call to :meth:`AnalysisSession.stream` runs one full analysis
turn against a freshly spawned Hermes ACP transport. The driver:

1. Synthesizes the leading ``ACCEPTED`` event.
2. Boots the transport and sends the prompt derived from
   :class:`AXAnalysisRequest`.
3. Translates each ACP ``session/update`` into an :class:`AXStreamEvent`
   via :class:`StageTranslator` and yields it.
4. Synthesizes a terminal ``COMPLETED`` whose ``data`` validates as the
   shared :class:`AXAnalysisResult` (``latency_ms`` plus a stage history
   embedded in ``analysis_summary``), or ``ERROR`` with one of the
   structured error codes below on failure:
     * ``HERMES_TIMEOUT`` — overall prompt budget exceeded.
     * ``HERMES_CRASHED`` — transport raised before reaching a clean end.

The driver is implemented as an async iterator so the FastAPI SSE route
can stream events directly to the gateway.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import datetime
from typing import AsyncIterator

from .config import BridgeSettings
from .contract import (
    AXAnalysisError,
    AXAnalysisRequest,
    AXAnalysisResult,
    AXStreamEvent,
    JobStatus,
    StreamStage,
)
from .hermes_client import HermesTransport
from .translate import StageTranslator

logger = logging.getLogger(__name__)


def _extract_structured_payload(text: str) -> object:
    """Best-effort JSON extraction from the skill's final assistant text.

    The ``acent-ax-analysis`` skill is asked to emit its result as a
    JSON block, usually wrapped in a triple-backtick ``json`` fence.
    The model occasionally adds chatty prose around the fence, or omits
    the fence entirely and just emits raw JSON.

    Returns the decoded object on success, or ``None`` if nothing
    JSON-shaped can be recovered. Never raises — a malformed payload
    just falls back to ``analysis_summary.summary`` rendering.
    """
    if not text:
        return None
    candidates: list[str] = []
    # Prefer the last fenced ```json ... ``` block (the skill's final answer
    # often arrives after intermediate fenced blocks).
    lowered = text.lower()
    end = len(text)
    while True:
        fence_close = text.rfind("```", 0, end)
        if fence_close == -1:
            break
        # Find the matching opening fence before this close.
        fence_open = text.rfind("```", 0, fence_close)
        if fence_open == -1:
            break
        block = text[fence_open + 3 : fence_close]
        # Strip an optional ``` "json" language tag from the first line.
        newline = block.find("\n")
        if newline != -1 and block[:newline].strip().lower() in ("json", ""):
            block = block[newline + 1 :]
        candidates.append(block.strip())
        end = fence_open
    # Also try the whole string, in case the model emitted raw JSON.
    candidates.append(text.strip())
    for candidate in candidates:
        if not candidate or candidate[0] not in "{[":
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


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
        final_text = self._translator.final_message_text.strip()
        analysis_summary: dict = {
            "stage_history": [s.value for s in self._translator.stage_history],
        }
        structured = _extract_structured_payload(final_text)
        if final_text:
            # Always preserve the raw LLM answer so FDK has something
            # to render even when the model did not emit a JSON block.
            analysis_summary["summary"] = final_text
        result_kwargs: dict = {
            "job_id": self._request.job_id,
            "tenant_id": self._request.tenant_id,
            "status": JobStatus.COMPLETED,
            "completed_at": datetime.now(),
            "analysis_summary": analysis_summary,
            "latency_ms": latency_ms,
        }
        if isinstance(structured, dict):
            # Lift contract-shaped fields out of the JSON block so FDK
            # can render dedicated sections (draft, review, tags,
            # suggested_status_change, recommendation).
            for key in (
                "draft_output",
                "review_output",
                "final_private_note_html",
                "final_private_note_text",
                "suggested_status_change",
                "suggested_tags",
                "confidence_score",
                "recommendation",
            ):
                value = structured.get(key)
                if value is not None:
                    result_kwargs[key] = value
            extra_summary = structured.get("analysis_summary")
            if isinstance(extra_summary, dict):
                analysis_summary.update(extra_summary)
        try:
            result = AXAnalysisResult(**result_kwargs)
        except Exception:
            logger.exception(
                "Failed to build typed AXAnalysisResult from skill output; "
                "falling back to bare summary"
            )
            result = AXAnalysisResult(
                job_id=self._request.job_id,
                tenant_id=self._request.tenant_id,
                status=JobStatus.COMPLETED,
                completed_at=datetime.now(),
                analysis_summary=analysis_summary,
                latency_ms=latency_ms,
            )
        yield AXStreamEvent(
            job_id=self._request.job_id,
            stage=StreamStage.COMPLETED,
            message="analysis pipeline finished",
            data=result.model_dump(mode="json"),
        )

    async def _run_until_terminal(self) -> AsyncIterator[AXStreamEvent]:
        timeout = self._settings.prompt_timeout_s
        deadline = asyncio.get_event_loop().time() + timeout

        await self._transport.start()
        prompt_text = build_prompt_text(self._request, skill=self._settings.hermes_skill)

        # ACP servers stream `session/update` notifications while a prompt is
        # in-flight but only send the matching `session/prompt` JSON-RPC
        # response at end-of-turn. Awaiting send_prompt before draining
        # session_updates would defer every stage event until after the turn
        # finishes, defeating modal-first progressive SSE. Run send_prompt as
        # a background task so the update iterator can yield concurrently.
        prompt_task: asyncio.Task[None] = asyncio.create_task(
            self._transport.send_prompt(prompt_text)
        )

        try:
            agen = self._transport.session_updates().__aiter__()
            while True:
                remaining = deadline - asyncio.get_event_loop().time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    update = await asyncio.wait_for(
                        agen.__anext__(), timeout=remaining
                    )
                except StopAsyncIteration:
                    break
                event = self._translator.translate(update)
                if event is not None:
                    yield event

            # session_updates ended cleanly; propagate any prompt-task error
            # within the remaining budget so we still surface HERMES_TIMEOUT
            # rather than hanging.
            remaining = max(0.0, deadline - asyncio.get_event_loop().time())
            await asyncio.wait_for(asyncio.shield(prompt_task), timeout=remaining)
        finally:
            if not prompt_task.done():
                prompt_task.cancel()
            with contextlib.suppress(BaseException):
                await prompt_task

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
