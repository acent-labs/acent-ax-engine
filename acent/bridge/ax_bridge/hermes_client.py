"""Hermes ACP transport.

Phase 1 invariant: one Hermes ACP subprocess per analysis request. Pool /
backpressure work is deferred to Phase 4 (see AXE roadmap).

The class hierarchy is intentionally thin so the session driver can be
unit-tested against an in-memory fake without spawning a real subprocess:

* ``HermesTransport`` — abstract async iterator over decoded ACP
  ``session/update`` notifications, plus ``send_prompt`` / ``aclose``.
* ``SubprocessHermesTransport`` — production implementation that spawns
  ``hermes acp`` and frames JSON-RPC over stdio.

Runtime smoke against a real Hermes binary is intentionally out of scope
for AXE-20 unit tests — see Workpad runtime-smoke notes for the deferred
integration path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol

from .config import BridgeSettings

logger = logging.getLogger(__name__)


class HermesProtocolError(RuntimeError):
    """Raised when Hermes returns a JSON-RPC ``error`` response."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"Hermes JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class HermesTransport(Protocol):
    """Minimal contract the analysis session driver depends on."""

    async def start(self) -> None:
        ...

    async def send_prompt(self, prompt_text: str) -> None:
        ...

    def session_updates(self) -> AsyncIterator[dict]:
        """Yield decoded ``session/update.params.update`` dicts."""
        ...

    async def aclose(self) -> None:
        ...


class SubprocessHermesTransport:
    """Spawn ``hermes acp`` and frame JSON-RPC over its stdio.

    ACP wraps each JSON message with an LSP-style ``Content-Length`` header.
    Hermes ships its own permission/auth/initialize handshake — those calls
    are issued from :meth:`start` before the prompt is sent.

    NOTE: this implementation is the production path. It is **not** exercised
    by the AXE-20 unit tests because the Hermes binary is not present in CI.
    Runtime smoke happens under ``acent/deployment/`` integration.
    """

    def __init__(self, settings: BridgeSettings) -> None:
        self._settings = settings
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._session_id: Optional[str] = None
        self._next_request_id: int = 0
        self._update_queue: asyncio.Queue[dict] = asyncio.Queue()
        self._pending: dict[int, asyncio.Future[object]] = {}
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._stopped = asyncio.Event()
        # Set when a session/prompt JSON-RPC response arrives. ACP keeps the
        # subprocess alive across turns, so session_updates() uses this signal
        # (queue drained AND prompt complete) to exit instead of waiting for
        # process exit.
        self._prompt_complete = asyncio.Event()

    # ------------------------------------------------------------------
    # Public transport interface
    # ------------------------------------------------------------------
    async def start(self) -> None:
        env = os.environ.copy()
        self._proc = await asyncio.create_subprocess_exec(
            *self._settings.hermes_command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self._settings.hermes_cwd,
            env=env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        await self._handshake()

    async def send_prompt(self, prompt_text: str) -> None:
        if not self._session_id:
            raise RuntimeError("ACP session not initialized")
        try:
            await self._request(
                "session/prompt",
                {
                    "sessionId": self._session_id,
                    "prompt": [{"type": "text", "text": prompt_text}],
                },
            )
        finally:
            # Signal the update consumer that this turn produced no more
            # session/update notifications, even though the subprocess is
            # still running.
            self._prompt_complete.set()

    async def session_updates(self) -> AsyncIterator[dict]:
        while not self._stopped.is_set():
            try:
                update = await asyncio.wait_for(self._update_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                if self._proc and self._proc.returncode is not None:
                    return
                # Clean turn completion: the prompt response arrived and we
                # have no more queued updates. The Hermes process stays alive
                # for future prompts, but this turn is done.
                if self._prompt_complete.is_set() and self._update_queue.empty():
                    return
                continue
            yield update

    async def aclose(self) -> None:
        self._stopped.set()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass

    # ------------------------------------------------------------------
    # ACP framing
    # ------------------------------------------------------------------
    async def _handshake(self) -> None:
        await self._request(
            "initialize",
            {"protocolVersion": 1, "clientCapabilities": {"fs": False}},
        )
        new_session = await self._request(
            "session/new",
            {"mcpServers": [], "cwd": self._settings.hermes_cwd or os.getcwd()},
        )
        if not isinstance(new_session, dict) or "sessionId" not in new_session:
            raise RuntimeError(f"Hermes session/new returned unexpected payload: {new_session!r}")
        self._session_id = new_session["sessionId"]

    async def _request(self, method: str, params: dict) -> object:
        if not self._proc or not self._proc.stdin:
            raise RuntimeError("Hermes subprocess not running")
        request_id = self._next_request_id
        self._next_request_id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        loop = asyncio.get_event_loop()
        future: asyncio.Future[object] = loop.create_future()
        self._pending[request_id] = future
        try:
            self._proc.stdin.write(header + body)
            await self._proc.stdin.drain()
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def _read_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        stdout = self._proc.stdout
        try:
            while not self._stopped.is_set():
                header = await stdout.readline()
                if not header:
                    return
                header_text = header.decode("ascii", errors="replace").strip()
                if not header_text.lower().startswith("content-length:"):
                    continue
                try:
                    length = int(header_text.split(":", 1)[1].strip())
                except (IndexError, ValueError):
                    continue
                await stdout.readline()  # consume blank line
                body = await stdout.readexactly(length)
                try:
                    message = json.loads(body)
                except json.JSONDecodeError:
                    logger.exception("Hermes ACP frame decode failed")
                    continue
                self._dispatch_message(message)
        finally:
            self._fail_pending(RuntimeError("Hermes ACP read loop terminated"))

    def _dispatch_message(self, message: dict) -> None:
        # JSON-RPC response: has "id" and either "result" or "error".
        if "id" in message and ("result" in message or "error" in message):
            request_id = message.get("id")
            if not isinstance(request_id, int):
                return
            future = self._pending.get(request_id)
            if future is None or future.done():
                return
            if "error" in message:
                err = message["error"] or {}
                future.set_exception(
                    HermesProtocolError(
                        code=int(err.get("code", -1)),
                        message=str(err.get("message", "unknown error")),
                        data=err.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result"))
            return

        # JSON-RPC notification: session/update is the streaming event channel.
        if message.get("method") == "session/update":
            params = message.get("params") or {}
            update = params.get("update")
            if isinstance(update, dict):
                self._update_queue.put_nowait(update)

    def _fail_pending(self, exc: BaseException) -> None:
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()


# Convenience factory used by the analyze route. Tests substitute this with a
# stub through dependency injection.
TransportFactory = Callable[[BridgeSettings], HermesTransport]


def default_transport_factory(settings: BridgeSettings) -> HermesTransport:
    return SubprocessHermesTransport(settings)


__all__ = [
    "HermesProtocolError",
    "HermesTransport",
    "SubprocessHermesTransport",
    "TransportFactory",
    "default_transport_factory",
]
