"""FastAPI server smoke (TestClient)."""
from __future__ import annotations

import json
from typing import AsyncIterator
from uuid import UUID

from fastapi.testclient import TestClient

from ax_bridge.config import BridgeSettings
from ax_bridge.hermes_client import HermesTransport
from ax_bridge.main import create_app


class _ScriptedTransport:
    def __init__(self, updates: list[dict]) -> None:
        self.updates = updates

    async def start(self) -> None:
        return None

    async def send_prompt(self, prompt_text: str) -> None:
        return None

    async def session_updates(self) -> AsyncIterator[dict]:
        for update in self.updates:
            yield update

    async def aclose(self) -> None:
        return None


def _settings() -> BridgeSettings:
    return BridgeSettings(ax_engine_internal_token="smoke-token", prompt_timeout_s=2.0)


def _make_client(updates: list[dict] | None = None) -> TestClient:
    settings = _settings()

    def factory(_: BridgeSettings) -> HermesTransport:
        return _ScriptedTransport(updates or [])

    app = create_app(settings=settings, transport_factory=factory)
    return TestClient(app)


def _request_body() -> dict:
    return {
        "job_id": str(UUID("00000000-0000-0000-0000-00000000aa01")),
        "correlation_id": "corr-1",
        "tenant_id": str(UUID("00000000-0000-0000-0000-0000000000ff")),
        "external_ticket_id": "t-42",
        "ticket_data": {"subject": "hi"},
    }


def test_healthz_is_unauthenticated_and_returns_ok() -> None:
    client = _make_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "acent-ax-bridge"


def test_analyze_rejects_missing_authorization_header() -> None:
    client = _make_client()
    response = client.post("/v1/analyze", json=_request_body())
    assert response.status_code == 401


def test_analyze_rejects_invalid_bearer_token() -> None:
    client = _make_client()
    response = client.post(
        "/v1/analyze",
        json=_request_body(),
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_analyze_rejects_invalid_request_body() -> None:
    client = _make_client()
    response = client.post(
        "/v1/analyze",
        json={"job_id": "not-a-uuid"},
        headers={"Authorization": "Bearer smoke-token"},
    )
    assert response.status_code == 422


def test_analyze_streams_accepted_then_completed_on_clean_run() -> None:
    client = _make_client(
        updates=[
            {"sessionUpdate": "tool_call", "toolCallId": "1", "title": "ax-analyzer"},
            {"sessionUpdate": "tool_call", "toolCallId": "2", "title": "ax-drafter"},
            {"sessionUpdate": "tool_call", "toolCallId": "3", "title": "ax-reviewer"},
        ]
    )
    with client.stream(
        "POST",
        "/v1/analyze",
        json=_request_body(),
        headers={"Authorization": "Bearer smoke-token"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = response.read().decode("utf-8")

    frames = [b for b in body.split("\n\n") if b.strip()]
    # Each frame begins with "event: <stage>"
    stages = []
    for frame in frames:
        first_line = frame.splitlines()[0]
        assert first_line.startswith("event: ")
        stages.append(first_line.removeprefix("event: "))
    assert stages[0] == "accepted"
    assert stages[-1] == "completed"

    # Last frame's data has latency_ms and stage_history
    last_frame = frames[-1]
    data_line = next(line for line in last_frame.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["data"]["stage_history"][0] == "fetching"
    assert "latency_ms" in payload["data"]
