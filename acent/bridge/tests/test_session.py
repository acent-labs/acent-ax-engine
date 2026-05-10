"""AnalysisSession terminal-path unit tests.

Acceptance criteria coverage (from AXE-20):
* terminal completed path (ACCEPTED first, COMPLETED last with stage_history)
* terminal error path: HERMES_TIMEOUT
* terminal error path: HERMES_CRASHED
"""
from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from ax_bridge.config import BridgeSettings
from ax_bridge.contract import AXAnalysisRequest, StreamStage
from ax_bridge.hermes_client import HermesTransport
from ax_bridge.session import AnalysisSession


class FakeTransport:
    """Scriptable HermesTransport stub.

    Pass a list of update dicts to replay. Optionally raise from `start()`,
    `send_prompt()`, or stall the iterator to exercise the timeout path.
    """

    def __init__(
        self,
        updates: list[dict] | None = None,
        *,
        crash_on_start: bool = False,
        stall_seconds: float | None = None,
    ) -> None:
        self.updates = updates or []
        self.crash_on_start = crash_on_start
        self.stall_seconds = stall_seconds
        self.started = False
        self.prompt: str | None = None
        self.closed = False

    async def start(self) -> None:
        if self.crash_on_start:
            raise RuntimeError("subprocess refused to start")
        self.started = True

    async def send_prompt(self, prompt_text: str) -> None:
        self.prompt = prompt_text

    async def session_updates(self) -> AsyncIterator[dict]:
        if self.stall_seconds is not None:
            await asyncio.sleep(self.stall_seconds)
        for update in self.updates:
            yield update

    async def aclose(self) -> None:
        self.closed = True


def _settings(timeout: float = 2.0) -> BridgeSettings:
    return BridgeSettings(ax_engine_internal_token="test-token", prompt_timeout_s=timeout)


def _request() -> AXAnalysisRequest:
    from uuid import UUID

    return AXAnalysisRequest(
        job_id=UUID("00000000-0000-0000-0000-00000000aa01"),
        correlation_id="corr-1",
        tenant_id=UUID("00000000-0000-0000-0000-0000000000ff"),
        external_ticket_id="t-42",
        ticket_data={"subject": "hi", "description_text": "broken"},
    )


async def _collect(session: AnalysisSession) -> list:
    return [event async for event in session.stream()]


@pytest.mark.asyncio
async def test_clean_run_emits_accepted_then_completed_with_history() -> None:
    transport = FakeTransport(
        updates=[
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "1",
                "title": "ax-analyzer task",
            },
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "2",
                "title": "ax-drafter task",
            },
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "3",
                "title": "ax-reviewer task",
            },
        ]
    )
    session = AnalysisSession(_request(), transport, _settings())
    events = await _collect(session)

    assert events[0].stage == StreamStage.ACCEPTED
    assert events[-1].stage == StreamStage.COMPLETED
    middle_stages = [e.stage for e in events[1:-1]]
    assert middle_stages == [
        StreamStage.ANALYZING,
        StreamStage.DRAFTING,
        StreamStage.REVIEWING,
    ]
    completed_data = events[-1].data or {}
    assert "latency_ms" in completed_data
    assert completed_data["stage_history"] == [
        StreamStage.FETCHING.value,
        StreamStage.ANALYZING.value,
        StreamStage.DRAFTING.value,
        StreamStage.REVIEWING.value,
    ]
    assert transport.started and transport.closed


@pytest.mark.asyncio
async def test_timeout_emits_error_with_hermes_timeout_code() -> None:
    transport = FakeTransport(stall_seconds=5.0)
    session = AnalysisSession(_request(), transport, _settings(timeout=0.1))
    events = await _collect(session)

    assert events[0].stage == StreamStage.ACCEPTED
    assert events[-1].stage == StreamStage.ERROR
    error_data = events[-1].data or {}
    assert error_data["error_code"] == "HERMES_TIMEOUT"
    assert error_data["should_retry"] is True
    assert transport.closed


@pytest.mark.asyncio
async def test_transport_crash_emits_hermes_crashed_error() -> None:
    transport = FakeTransport(crash_on_start=True)
    session = AnalysisSession(_request(), transport, _settings())
    events = await _collect(session)

    assert events[0].stage == StreamStage.ACCEPTED
    assert events[-1].stage == StreamStage.ERROR
    error_data = events[-1].data or {}
    assert error_data["error_code"] == "HERMES_CRASHED"
    assert error_data["should_retry"] is False
    assert "subprocess refused to start" in error_data["message"]
    assert transport.closed


@pytest.mark.asyncio
async def test_stream_starts_with_accepted_and_ends_with_terminal() -> None:
    transport = FakeTransport(updates=[])
    session = AnalysisSession(_request(), transport, _settings())
    events = await _collect(session)

    assert events[0].stage == StreamStage.ACCEPTED
    assert events[-1].stage in {StreamStage.COMPLETED, StreamStage.ERROR}


@pytest.mark.asyncio
async def test_session_implements_hermes_transport_protocol() -> None:
    transport: HermesTransport = FakeTransport()
    assert hasattr(transport, "start")
    assert hasattr(transport, "send_prompt")
    assert hasattr(transport, "session_updates")
    assert hasattr(transport, "aclose")
