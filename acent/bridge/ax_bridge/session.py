"""Per-request Hermes ACP session driver.

Spawns a ``hermes-agent`` subprocess via the ACP package, walks through
the canonical ACP handshake (initialize → new session → prompt), and
exposes the resulting session-update stream as :class:`AXStreamEvent`s.

Phase 1 is intentionally a per-request spawn: simple, slow, but
isolation-safe. A pool / shared-process model is a Phase 4 concern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from ax_bridge.config import Settings
from ax_bridge.contract import (
    AXAnalysisRequest,
    AXErrorInfo,
    AXStreamEvent,
    StreamStage,
)
from ax_bridge.hermes_client import HermesACPClient
from ax_bridge.translate import new_context, translate_session_update

logger = logging.getLogger(__name__)


def _build_skill_prompt(request: AXAnalysisRequest, skill: str) -> str:
    """Render the skill-invocation message sent to Hermes.

    Hermes' CLI / ACP accepts ``/skill-name`` as a prompt prefix to
    activate a skill, followed by free-form arguments. We pass the full
    AXAnalysisRequest as JSON so the orchestrator skill can consume it
    verbatim.
    """

    payload_json = request.model_dump_json(by_alias=False)
    return f"/{skill} " + payload_json


def _build_env(settings: Settings) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in settings.hermes_env_passthrough:
        if key in os.environ:
            env[key] = os.environ[key]
    return env


@asynccontextmanager
async def _spawned_hermes(settings: Settings, client: HermesACPClient):
    """Yield ``(connection, process)`` from acp.spawn_agent_process."""

    # Imported here so the module remains import-safe in environments
    # without the ACP package (e.g. translator unit tests).
    from acp import spawn_agent_process

    env = _build_env(settings)
    cwd = settings.hermes_cwd or None

    async with spawn_agent_process(
        client,
        settings.hermes_command,
        *settings.hermes_args,
        env=env,
        cwd=cwd,
    ) as (connection, process):
        yield connection, process


async def _initialize_and_prompt(
    connection: Any,
    request: AXAnalysisRequest,
    settings: Settings,
) -> None:
    """Run ACP handshake and fire the prompt. Errors propagate."""

    from acp.schema import ClientCapabilities, NewSessionRequest, PromptRequest, TextContentBlock

    # Handshake
    await connection.initialize(
        protocol_version=1,
        client_capabilities=ClientCapabilities(
            fs={"readTextFile": False, "writeTextFile": False},
            terminal=False,
        ),
    )

    new_session = await connection.new_session(
        cwd=settings.hermes_cwd or os.getcwd(),
        mcp_servers=[],
    )
    session_id = new_session.session_id

    prompt_text = _build_skill_prompt(request, settings.acent_skill_name)
    await connection.prompt(
        session_id=session_id,
        prompt=[TextContentBlock(text=prompt_text)],
    )


async def stream_analysis(
    request: AXAnalysisRequest,
    settings: Settings,
) -> AsyncIterator[AXStreamEvent]:
    """Drive a one-shot Hermes analysis and yield AXStreamEvent stream.

    Closes with either ``COMPLETED`` or ``ERROR``. The caller is
    responsible for SSE-encoding and forwarding to the FastAPI client.
    """

    run_id = str(uuid.uuid4())
    started = time.perf_counter()
    ctx = new_context(run_id)
    client = HermesACPClient()
    error_info: AXErrorInfo | None = None
    completed = False

    async def _run_session() -> None:
        try:
            async with _spawned_hermes(settings, client) as (connection, _proc):
                await asyncio.wait_for(
                    _initialize_and_prompt(connection, request, settings),
                    timeout=settings.spawn_timeout_s,
                )
                # Hermes will push session updates via client.session_update
                # until the prompt completes. Cap the total runtime.
                await asyncio.wait_for(
                    connection.wait_for_prompt_completion(),
                    timeout=settings.prompt_timeout_s,
                )
        finally:
            await client.close()

    session_task = asyncio.create_task(_run_session())

    try:
        async for queued in client.updates():
            update = queued.get("update", {})
            for event in translate_session_update(update, ctx):
                yield event

        # Session task should be done (or about to be) once the queue closes.
        await session_task
        completed = True
    except asyncio.TimeoutError as exc:
        error_info = AXErrorInfo(
            stage=ctx.current_stage,
            error_code="HERMES_TIMEOUT",
            message=str(exc) or "Hermes prompt exceeded timeout",
            should_retry=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[bridge] hermes session crashed run=%s", run_id)
        error_info = AXErrorInfo(
            stage=ctx.current_stage,
            error_code="HERMES_CRASHED",
            message=str(exc) or exc.__class__.__name__,
            should_retry=True,
        )
        if not session_task.done():
            session_task.cancel()
            try:
                await session_task
            except (asyncio.CancelledError, Exception):
                pass

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if completed and error_info is None:
        # The orchestrator skill is expected to emit a structured COMPLETED
        # payload as its final message. The translator is heuristic-only,
        # so we materialize a synthetic terminal event with the contract's
        # expected shape — extracted from ctx.stage_history if present.
        yield AXStreamEvent(
            run_id=run_id,
            sequence=ctx.next_seq(),
            stage=StreamStage.COMPLETED,
            payload={"latency_ms": elapsed_ms, "stage_history": [s.value for s in ctx.stage_history]},
        )
    else:
        err = error_info or AXErrorInfo(
            stage=ctx.current_stage,
            error_code="HERMES_INCOMPLETE",
            message="session ended without explicit completion",
        )
        yield AXStreamEvent(
            run_id=run_id,
            sequence=ctx.next_seq(),
            stage=StreamStage.ERROR,
            payload={"error": err.model_dump(mode="json"), "latency_ms": elapsed_ms},
        )


__all__ = ["stream_analysis"]
