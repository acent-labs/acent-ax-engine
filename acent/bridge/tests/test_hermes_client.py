"""Unit tests for SubprocessHermesTransport JSON-RPC framing.

These tests do not spawn a real Hermes binary. Instead we wire the transport
to an in-memory stdout (asyncio.StreamReader) and a capturing stdin, then
drive the read loop by feeding ACP frames byte-for-byte. This exercises the
production response-matching path that AXE-20 acceptance requires.
"""
from __future__ import annotations

import asyncio
import json
from typing import List

import pytest

from ax_bridge.config import BridgeSettings
from ax_bridge.hermes_client import HermesProtocolError, SubprocessHermesTransport


def _frame(payload: dict) -> bytes:
    """Encode one NDJSON record (JSON object + trailing newline)."""
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


class _CapturingStdin:
    """Minimal stdin stand-in that records the bytes we write to it."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


class _FakeProc:
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


def _decode_frames(buf: bytes) -> List[dict]:
    """Parse all NDJSON records (one JSON object per '\\n'-terminated line) out of ``buf``."""
    frames: List[dict] = []
    for line in buf.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        frames.append(json.loads(stripped))
    return frames


def _wire(transport: SubprocessHermesTransport) -> tuple[_CapturingStdin, asyncio.StreamReader]:
    stdin = _CapturingStdin()
    stdout = asyncio.StreamReader(limit=2**20)
    transport._proc = _FakeProc(stdin, stdout)  # type: ignore[assignment]
    transport._reader_task = asyncio.create_task(transport._read_loop())
    return stdin, stdout


@pytest.fixture
def settings() -> BridgeSettings:
    return BridgeSettings(ax_engine_internal_token="test-token", prompt_timeout_s=2.0)


@pytest.mark.asyncio
async def test_request_returns_matching_jsonrpc_result(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    stdin, stdout = _wire(transport)
    try:
        request_task = asyncio.create_task(transport._request("initialize", {"protocolVersion": 1}))
        # Let the write hit stdin so we can read back the request id.
        await asyncio.sleep(0)
        sent = _decode_frames(bytes(stdin.buffer))
        assert len(sent) == 1
        request_id = sent[0]["id"]
        assert sent[0]["method"] == "initialize"
        assert sent[0]["params"] == {"protocolVersion": 1}

        stdout.feed_data(_frame({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": 1}}))
        result = await asyncio.wait_for(request_task, timeout=1.0)
        assert result == {"protocolVersion": 1}
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_request_raises_on_jsonrpc_error(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    stdin, stdout = _wire(transport)
    try:
        request_task = asyncio.create_task(transport._request("session/new", {"cwd": "/"}))
        await asyncio.sleep(0)
        request_id = _decode_frames(bytes(stdin.buffer))[0]["id"]

        stdout.feed_data(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32600, "message": "Invalid Request", "data": {"hint": "x"}},
                }
            )
        )
        with pytest.raises(HermesProtocolError) as exc_info:
            await asyncio.wait_for(request_task, timeout=1.0)
        assert exc_info.value.code == -32600
        assert "Invalid Request" in exc_info.value.message
        assert exc_info.value.data == {"hint": "x"}
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_concurrent_requests_match_by_id(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    stdin, stdout = _wire(transport)
    try:
        task_a = asyncio.create_task(transport._request("a", {}))
        task_b = asyncio.create_task(transport._request("b", {}))
        await asyncio.sleep(0)
        sent = _decode_frames(bytes(stdin.buffer))
        assert {f["method"] for f in sent} == {"a", "b"}
        id_by_method = {f["method"]: f["id"] for f in sent}

        # Reply out of order: b first, then a.
        stdout.feed_data(_frame({"jsonrpc": "2.0", "id": id_by_method["b"], "result": "B"}))
        stdout.feed_data(_frame({"jsonrpc": "2.0", "id": id_by_method["a"], "result": "A"}))

        result_b = await asyncio.wait_for(task_b, timeout=1.0)
        result_a = await asyncio.wait_for(task_a, timeout=1.0)
        assert result_a == "A"
        assert result_b == "B"
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_session_update_notification_routed_to_queue(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    _stdin, stdout = _wire(transport)
    try:
        update_payload = {"sessionUpdate": "agent_message_chunk", "content": {"text": "hi"}}
        stdout.feed_data(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": "s-1", "update": update_payload},
                }
            )
        )
        await asyncio.sleep(0)
        update = await asyncio.wait_for(transport._update_queue.get(), timeout=1.0)
        assert update == update_payload
        # Notifications must not corrupt the pending-request map.
        assert transport._pending == {}
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_handshake_uses_returned_session_id(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    stdin, stdout = _wire(transport)
    try:
        handshake_task = asyncio.create_task(transport._handshake())
        # Drain the initialize request, reply, then the session/new request.
        async def _drive() -> None:
            for expected_method, reply in [
                ("initialize", {"protocolVersion": 1}),
                ("session/new", {"sessionId": "sess-xyz"}),
            ]:
                deadline = asyncio.get_event_loop().time() + 1.0
                while True:
                    sent = _decode_frames(bytes(stdin.buffer))
                    matching = [f for f in sent if f["method"] == expected_method]
                    if matching:
                        stdout.feed_data(
                            _frame({"jsonrpc": "2.0", "id": matching[0]["id"], "result": reply})
                        )
                        break
                    if asyncio.get_event_loop().time() > deadline:
                        raise AssertionError(f"never saw {expected_method}")
                    await asyncio.sleep(0.01)

        await asyncio.wait_for(_drive(), timeout=2.0)
        await asyncio.wait_for(handshake_task, timeout=1.0)
        assert transport._session_id == "sess-xyz"
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_handshake_raises_when_session_new_payload_invalid(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    stdin, stdout = _wire(transport)
    try:
        handshake_task = asyncio.create_task(transport._handshake())

        async def _drive() -> None:
            for expected_method, reply in [
                ("initialize", {"protocolVersion": 1}),
                ("session/new", {"unexpected": "payload"}),  # missing sessionId
            ]:
                deadline = asyncio.get_event_loop().time() + 1.0
                while True:
                    sent = _decode_frames(bytes(stdin.buffer))
                    matching = [f for f in sent if f["method"] == expected_method]
                    if matching:
                        stdout.feed_data(
                            _frame({"jsonrpc": "2.0", "id": matching[0]["id"], "result": reply})
                        )
                        break
                    if asyncio.get_event_loop().time() > deadline:
                        raise AssertionError(f"never saw {expected_method}")
                    await asyncio.sleep(0.01)

        await asyncio.wait_for(_drive(), timeout=2.0)
        with pytest.raises(RuntimeError, match="Hermes session/new returned unexpected payload"):
            await asyncio.wait_for(handshake_task, timeout=1.0)
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_pending_request_fails_when_read_loop_terminates(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    _stdin, stdout = _wire(transport)
    try:
        request_task = asyncio.create_task(transport._request("session/prompt", {}))
        await asyncio.sleep(0)
        # Simulate Hermes closing stdout (process died).
        stdout.feed_eof()
        with pytest.raises(RuntimeError, match="read loop terminated"):
            await asyncio.wait_for(request_task, timeout=1.0)
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_send_prompt_requires_initialized_session(settings: BridgeSettings) -> None:
    transport = SubprocessHermesTransport(settings)
    _stdin, _stdout = _wire(transport)
    try:
        with pytest.raises(RuntimeError, match="ACP session not initialized"):
            await transport.send_prompt("hello")
    finally:
        await transport.aclose()


@pytest.mark.asyncio
async def test_session_updates_exits_after_prompt_response_with_alive_process(
    settings: BridgeSettings,
) -> None:
    """Regression for AXE-20 Codex review (2026-05-10T14:04Z).

    When the ``session/prompt`` JSON-RPC response arrives but the Hermes
    subprocess remains alive (the production ACP server stays up across
    turns), ``session_updates()`` must drain queued updates and exit cleanly.
    Previously it polled forever waiting for ``returncode != None``, causing
    AnalysisSession to fall through to ``HERMES_TIMEOUT``.
    """
    transport = SubprocessHermesTransport(settings)
    stdin, stdout = _wire(transport)
    transport._session_id = "sess-1"  # bypass handshake
    try:
        # Server emits an update notification, then the prompt response.
        update_payload = {
            "sessionUpdate": "tool_call",
            "toolCallId": "1",
            "title": "ax-analyzer task",
        }
        stdout.feed_data(
            _frame(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"sessionId": "sess-1", "update": update_payload},
                }
            )
        )

        prompt_task = asyncio.create_task(transport.send_prompt("hello"))
        # Wait until the request hits stdin so we can echo a matching response.
        deadline = asyncio.get_event_loop().time() + 1.0
        while True:
            sent = _decode_frames(bytes(stdin.buffer))
            matching = [f for f in sent if f["method"] == "session/prompt"]
            if matching:
                stdout.feed_data(
                    _frame(
                        {
                            "jsonrpc": "2.0",
                            "id": matching[0]["id"],
                            "result": {"stopReason": "end_turn"},
                        }
                    )
                )
                break
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError("never saw session/prompt request")
            await asyncio.sleep(0.01)

        await asyncio.wait_for(prompt_task, timeout=1.0)
        # Process is still alive — this is the production ACP behavior.
        assert transport._proc is not None
        assert transport._proc.returncode is None
        assert transport._prompt_complete.is_set()

        # session_updates() must drain the queued update and then exit
        # without waiting for the process to die.
        async def _drain() -> list[dict]:
            return [u async for u in transport.session_updates()]

        updates = await asyncio.wait_for(_drain(), timeout=1.0)
        assert updates == [update_payload]
    finally:
        await transport.aclose()
