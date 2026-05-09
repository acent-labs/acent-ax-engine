"""Wire contract between acent-flow FastAPI and the Hermes AX Engine.

⚠️  VENDORED COPY — keep in sync with
    https://github.com/acent-labs/acent-flow/blob/main/app/services/ax_engine_hermes/contract.py
    Bump ``CONTRACT_VERSION`` and update both sides in lock-step.

Enums mirror the CHECK constraints in
``acent-flow:supabase/migrations/20260509000000_create_ax_engine_hermes_schema.sql``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


CONTRACT_VERSION = "v1"


# ---------------------------------------------------------------------------
# Enums (must stay in sync with DB CHECK constraints)
# ---------------------------------------------------------------------------


class TriggerSource(StrEnum):
    MODAL = "modal"                 # 1차: 상담원 FDK 클릭
    SCHEDULED = "scheduled"         # 2차: cron prefetch
    ADMIN_MANUAL = "admin_manual"   # 2차: admin/CLI 수동 트리거


class DeliveryMode(StrEnum):
    STREAM_ONLY = "stream_only"             # 모달 스트림만 (modal 기본)
    NOTE_ONLY = "note_only"                 # 프라이빗 노트만 (scheduled 기본)
    STREAM_AND_NOTE = "stream_and_note"     # 둘 다


class JobStatus(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    ANALYZING = "analyzing"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WritebackStatus(StrEnum):
    SKIPPED = "skipped"             # stream_only 모드 고정값
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class StreamStage(StrEnum):
    """Stages emitted as SSE events by the AX Engine.

    Distinct from :class:`JobStatus` because some lifecycle states (queue
    pending, cancelled, etc.) are not user-observable in the modal flow.
    """

    ACCEPTED = "accepted"      # request acknowledged by engine
    FETCHING = "fetching"      # loading auxiliary context
    ANALYZING = "analyzing"    # analyzer agent running
    DRAFTING = "drafting"      # drafter agent running
    REVIEWING = "reviewing"    # reviewer agent running
    COMPLETED = "completed"    # terminal — final result emitted
    ERROR = "error"            # terminal — failure


class Recommendation(StrEnum):
    AUTO_RESOLVE = "auto_resolve"
    SUGGEST_REPLY = "suggest_reply"
    ESCALATE = "escalate"
    REVIEW = "review"
    NEEDS_INFO = "needs_info"


class Platform(StrEnum):
    FRESHDESK = "freshdesk"
    ZENDESK = "zendesk"
    INTERCOM = "intercom"
    WEB = "web"


# ---------------------------------------------------------------------------
# Request payload (FastAPI → AX Engine)
# ---------------------------------------------------------------------------


class TicketRequester(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Optional[str] = None
    name: Optional[str] = None
    locale: Optional[str] = None


class TicketMessage(BaseModel):
    """Single message in the ticket conversation thread."""

    model_config = ConfigDict(extra="forbid")

    author_type: str = Field(description="'requester' | 'agent' | 'system'")
    author_name: Optional[str] = None
    body_text: str
    created_at: datetime


class TicketData(BaseModel):
    """The fully-resolved ticket payload prepared by FastAPI for the engine.

    The engine is sandboxed and never calls Freshdesk; everything it needs
    must already be inside this object.
    """

    model_config = ConfigDict(extra="forbid")

    subject: str
    description_text: str
    description_html: Optional[str] = None
    status: Optional[str] = None
    priority: Optional[int] = None
    tags: list[str] = Field(default_factory=list)
    requester: Optional[TicketRequester] = None
    conversation: list[TicketMessage] = Field(default_factory=list)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: Optional[str] = None
    language: Optional[str] = None
    custom_system_prompt: Optional[str] = None
    max_attempts: int = Field(default=3, ge=1)


class AXAnalysisRequest(BaseModel):
    """Request body for ``POST /v1/analyze`` and queue payload."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default=CONTRACT_VERSION)

    tenant_id: str
    external_ticket_id: str
    external_ticket_display_id: Optional[str] = None
    platform: Platform = Platform.FRESHDESK

    trigger_source: TriggerSource = TriggerSource.MODAL
    delivery_mode: DeliveryMode = DeliveryMode.STREAM_ONLY
    pipeline_version: str = "v1"

    ticket_data: TicketData
    settings: AnalysisSettings = Field(default_factory=AnalysisSettings)


# ---------------------------------------------------------------------------
# Stream events (AX Engine → FastAPI → FDK)
# ---------------------------------------------------------------------------


class AXStreamEvent(BaseModel):
    """One SSE event in the analyze stream.

    ``payload`` content varies by ``stage``; consumers should read it
    defensively. Terminal events are ``COMPLETED`` (with full result) or
    ``ERROR`` (with ``error_info``).
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default=CONTRACT_VERSION)
    run_id: str
    sequence: int = Field(ge=0)
    stage: StreamStage
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Stage outputs (carried in stream events and persisted to analysis_results)
# ---------------------------------------------------------------------------


class AnalysisSummary(BaseModel):
    """Output of the Analyzer agent."""

    model_config = ConfigDict(extra="forbid")

    intent: Optional[str] = None
    sentiment: Optional[str] = None
    urgency_score: Optional[float] = Field(default=None, ge=0, le=1)
    category: Optional[str] = None
    escalation_risk: Optional[bool] = None
    language: Optional[str] = None
    key_entities: list[str] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class DraftOutput(BaseModel):
    """Output of the Drafter agent."""

    model_config = ConfigDict(extra="forbid")

    private_note_html: Optional[str] = None
    private_note_text: Optional[str] = None
    suggested_tags: list[str] = Field(default_factory=list)
    suggested_status: Optional[str] = None
    recommended_action: Optional[Recommendation] = None
    confidence: Optional[float] = Field(default=None, ge=0, le=1)


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


class ReviewOutput(BaseModel):
    """Output of the Reviewer agent (quality gate)."""

    model_config = ConfigDict(extra="forbid")

    verdict: ReviewVerdict
    reason: Optional[str] = None
    issues: list[str] = Field(default_factory=list)


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens_input: Optional[int] = Field(default=None, ge=0)
    tokens_output: Optional[int] = Field(default=None, ge=0)
    model_name: Optional[str] = None


class AXErrorInfo(BaseModel):
    """Carried in ``stage='error'`` payloads and persisted to ``error_info``."""

    model_config = ConfigDict(extra="forbid")

    stage: StreamStage
    error_code: str
    message: str
    should_retry: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Final result (persisted to ax_engine_hermes.analysis_results)
# ---------------------------------------------------------------------------


class AXAnalysisResult(BaseModel):
    """Terminal payload of a ``COMPLETED`` stream event.

    Mirrors a row in ``ax_engine_hermes.analysis_results`` (final columns).
    """

    model_config = ConfigDict(extra="forbid")

    contract_version: str = Field(default=CONTRACT_VERSION)
    run_id: str
    tenant_id: str
    external_ticket_id: str

    analysis_summary: Optional[AnalysisSummary] = None
    draft_output: Optional[DraftOutput] = None
    review_output: Optional[ReviewOutput] = None

    final_private_note_html: Optional[str] = None
    final_private_note_text: Optional[str] = None
    suggested_status_change: Optional[str] = None
    suggested_tags: list[str] = Field(default_factory=list)

    confidence_score: Optional[float] = Field(default=None, ge=0, le=1)
    recommendation: Recommendation = Recommendation.REVIEW

    usage: TokenUsage = Field(default_factory=TokenUsage)
    latency_ms: Optional[int] = Field(default=None, ge=0)


class AXEnqueueResult(BaseModel):
    """Response of ``AXEngineClient.enqueue_analysis`` (non-modal trigger)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    accepted: bool
    reason: Optional[str] = None


__all__ = [
    "CONTRACT_VERSION",
    # enums
    "TriggerSource",
    "DeliveryMode",
    "JobStatus",
    "WritebackStatus",
    "StreamStage",
    "Recommendation",
    "Platform",
    "ReviewVerdict",
    # ticket payload
    "TicketRequester",
    "TicketMessage",
    "TicketData",
    "AnalysisSettings",
    "AXAnalysisRequest",
    # stream
    "AXStreamEvent",
    # stage outputs
    "AnalysisSummary",
    "DraftOutput",
    "ReviewOutput",
    "TokenUsage",
    "AXErrorInfo",
    # results
    "AXAnalysisResult",
    "AXEnqueueResult",
]
