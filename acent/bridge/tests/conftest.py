"""Shared test fixtures."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import pytest

from ax_bridge.config import BridgeSettings
from ax_bridge.contract import AXAnalysisRequest


JOB_ID = UUID("00000000-0000-0000-0000-00000000aa01")
TENANT_ID = UUID("00000000-0000-0000-0000-0000000000ff")


@pytest.fixture
def settings() -> BridgeSettings:
    return BridgeSettings(
        ax_engine_internal_token="test-token",
        prompt_timeout_s=2.0,
    )


@pytest.fixture
def request_payload() -> AXAnalysisRequest:
    return AXAnalysisRequest(
        job_id=JOB_ID,
        correlation_id="corr-1",
        tenant_id=TENANT_ID,
        external_ticket_id="t-42",
        ticket_data={"subject": "hi", "description_text": "broken"},
        request_timestamp=datetime(2026, 5, 10, 0, 0, 0),
    )
