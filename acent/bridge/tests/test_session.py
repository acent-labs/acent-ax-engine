"""AnalysisSession terminal-path unit tests.

Acceptance criteria coverage (from AXE-20):
* terminal completed path (ACCEPTED first, COMPLETED last with stage_history)
* terminal error path: HERMES_TIMEOUT
* terminal error path: HERMES_CRASHED
* terminal completed path against the production ``SubprocessHermesTransport``
  with the subprocess kept alive after the prompt response (regression for
  the 2026-05-10T14:04Z Codex review).
"""
from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

import pytest

from ax_bridge.config import BridgeSettings
from ax_bridge.contract import (
    AXAnalysisRequest,
    AXAnalysisResult,
    JobStatus,
    StreamStage,
)
from ax_bridge.hermes_client import HermesTransport, SubprocessHermesTransport
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
    # Terminal completed.data must validate as the shared AXAnalysisResult contract.
    result = AXAnalysisResult.model_validate(completed_data)
    assert result.job_id == _request().job_id
    assert result.tenant_id == _request().tenant_id
    assert result.status == JobStatus.COMPLETED
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.analysis_summary == {
        "stage_history": [
            StreamStage.FETCHING.value,
            StreamStage.ANALYZING.value,
            StreamStage.DRAFTING.value,
            StreamStage.REVIEWING.value,
        ]
    }
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
async def test_completed_event_data_validates_as_ax_analysis_result() -> None:
    """Regression: completed.data MUST validate as the shared AXAnalysisResult.

    Codex review (2026-05-11T06:19Z) found the bridge synthesizing a terminal
    completed event with only {latency_ms, stage_history}, which is not a
    valid AXAnalysisResult. This test locks the contract shape independently
    of any field-by-field assertion.
    """
    request = _request()
    transport = FakeTransport(
        updates=[
            {"sessionUpdate": "tool_call", "toolCallId": "1", "title": "ax-analyzer"},
            {"sessionUpdate": "tool_call", "toolCallId": "2", "title": "ax-drafter"},
            {"sessionUpdate": "tool_call", "toolCallId": "3", "title": "ax-reviewer"},
        ]
    )
    session = AnalysisSession(request, transport, _settings())
    events = await _collect(session)

    completed = events[-1]
    assert completed.stage == StreamStage.COMPLETED
    result = AXAnalysisResult.model_validate(completed.data)
    assert result.job_id == request.job_id
    assert result.tenant_id == request.tenant_id
    assert result.status == JobStatus.COMPLETED
    assert result.completed_at is not None
    assert result.analysis_summary is not None
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_session_implements_hermes_transport_protocol() -> None:
    transport: HermesTransport = FakeTransport()
    assert hasattr(transport, "start")
    assert hasattr(transport, "send_prompt")
    assert hasattr(transport, "session_updates")
    assert hasattr(transport, "aclose")


# --- Integration: AnalysisSession × SubprocessHermesTransport ---------------
#
# Reproduces the Codex 2026-05-10T14:04Z scenario: the production transport
# returns clean responses for initialize/session/new/session/prompt and emits
# session/update notifications, but the subprocess stays alive afterwards
# (real ACP servers do). The stream must terminate with COMPLETED — not
# HERMES_TIMEOUT.


def _frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _decode_frames(buf: bytes) -> list[dict]:
    frames: list[dict] = []
    i = 0
    while i < len(buf):
        eol = buf.find(b"\r\n", i)
        if eol == -1:
            break
        header = buf[i:eol].decode("ascii")
        i = eol + 2
        if buf[i : i + 2] == b"\r\n":
            i += 2
        else:
            break
        length = int(header.split(":", 1)[1].strip())
        body = buf[i : i + length]
        i += length
        frames.append(json.loads(body))
    return frames


class _CapturingStdin:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


class _AliveFakeProc:
    """Process stand-in that stays alive across turns (production ACP)."""

    def __init__(self, stdin: _CapturingStdin, stdout: asyncio.StreamReader) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.returncode: int | None = None

    def terminate(self) -> None:
        self.returncode = 0
        self.stdout.feed_eof()

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        return self.returncode or 0


async def _await_request(
    stdin: _CapturingStdin, method: str, *, timeout: float = 2.0
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        for frame in _decode_frames(bytes(stdin.buffer)):
            if frame.get("method") == method:
                return frame
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"never saw {method} request")
        await asyncio.sleep(0.005)


async def _drive_acp_server(
    stdin: _CapturingStdin,
    stdout: asyncio.StreamReader,
    *,
    updates: list[dict],
) -> None:
    """Reply to handshake, then to session/prompt with interleaved updates."""

    init = await _await_request(stdin, "initialize")
    stdout.feed_data(
        _frame(
            {"jsonrpc": "2.0", "id": init["id"], "result": {"protocolVersion": 1}}
        )
    )
    new_session = await _await_request(stdin, "session/new")
    stdout.feed_data(
        _frame(
            {
                "jsonrpc": "2.0",
                "id": new_session["id"],
                "result": {"sessionId": "sess-1"},
            }
        )
    )
    prompt = await _await_request(stdin, "session/prompt")
    for update in updates:
        stdout.feed_data(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": "sess-1", "update": update},
                }
            )
        )
    stdout.feed_data(
        _frame(
            {
                "jsonrpc": "2.0",
                "id": prompt["id"],
                "result": {"stopReason": "end_turn"},
            }
        )
    )


@pytest.mark.asyncio
async def test_subprocess_transport_reaches_completed_with_alive_process() -> None:
    settings = BridgeSettings(
        ax_engine_internal_token="test-token", prompt_timeout_s=2.0
    )
    transport = SubprocessHermesTransport(settings)
    stdin = _CapturingStdin()
    stdout = asyncio.StreamReader(limit=2**20)
    transport._proc = _AliveFakeProc(stdin, stdout)  # type: ignore[assignment]

    # Override start() to skip the real subprocess spawn but keep the
    # production handshake (initialize + session/new) running.
    async def _fake_start() -> None:
        transport._reader_task = asyncio.create_task(transport._read_loop())
        await transport._handshake()

    transport.start = _fake_start  # type: ignore[method-assign]

    updates = [
        {"sessionUpdate": "tool_call", "toolCallId": "1", "title": "ax-analyzer task"},
        {"sessionUpdate": "tool_call", "toolCallId": "2", "title": "ax-drafter task"},
        {"sessionUpdate": "tool_call", "toolCallId": "3", "title": "ax-reviewer task"},
    ]
    server_task = asyncio.create_task(_drive_acp_server(stdin, stdout, updates=updates))

    session = AnalysisSession(_request(), transport, settings)

    events = await asyncio.wait_for(_collect(session), timeout=5.0)
    await asyncio.wait_for(server_task, timeout=1.0)

    # Process must still have been alive through the entire prompt turn.
    # (aclose() in the session finally block will mark it terminated, so we
    # can no longer observe returncode is None here, but the stream must end
    # with COMPLETED — not HERMES_TIMEOUT or HERMES_CRASHED.)
    assert events[0].stage == StreamStage.ACCEPTED
    assert events[-1].stage == StreamStage.COMPLETED, [
        (e.stage, (e.data or {}).get("error_code")) for e in events
    ]
    middle_stages = [e.stage for e in events[1:-1]]
    assert middle_stages == [
        StreamStage.ANALYZING,
        StreamStage.DRAFTING,
        StreamStage.REVIEWING,
    ]
    completed_data = events[-1].data or {}
    result = AXAnalysisResult.model_validate(completed_data)
    assert result.status == JobStatus.COMPLETED
    assert result.latency_ms is not None
    assert result.analysis_summary == {
        "stage_history": [
            StreamStage.FETCHING.value,
            StreamStage.ANALYZING.value,
            StreamStage.DRAFTING.value,
            StreamStage.REVIEWING.value,
        ]
    }
