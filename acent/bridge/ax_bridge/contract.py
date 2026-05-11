"""
Hermes AX Engine wire contract.

Shared Pydantic v2 models for FastAPI ↔ Hermes communication.
All models reject unknown fields for strict schema validation.

Version: v1

Vendored from
`acent-labs/acent-flow:app/services/ax_engine_hermes/contract.py` (AXE-22),
modulo trailing whitespace cleanup so `git diff --check` stays green.
Keep the two copies in lock-step — the bridge is the receiver of the exact
same wire models that acent-flow FastAPI emits.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

# -----------------------------------------------------------------------------
# Enums matching database CHECK constraints
# -----------------------------------------------------------------------------


class TriggerSource(StrEnum):
    """Source that triggered the analysis request."""
    MODAL = "modal"
    SCHEDULED = "scheduled"
    ADMIN_MANUAL = "admin_manual"


class DeliveryMode(StrEnum):
    """How results should be delivered."""
    STREAM_ONLY = "stream_only"
    NOTE_ONLY = "note_only"
    STREAM_AND_NOTE = "stream_and_note"


class JobStatus(StrEnum):
    """Analysis job lifecycle."""
    PENDING = "pending"
    FETCHING = "fetching"
    ANALYZING = "analyzing"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WritebackStatus(StrEnum):
    """Status of write-back to Freshdesk."""
    SKIPPED = "skipped"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class StreamStage(StrEnum):
    """SSE stream event stages."""
    ACCEPTED = "accepted"
    FETCHING = "fetching"
    ANALYZING = "analyzing"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    ERROR = "error"


class Recommendation(StrEnum):
    """Final recommendation from analysis."""
    AUTO_RESOLVE = "auto_resolve"
    SUGGEST_REPLY = "suggest_reply"
    ESCALATE = "escalate"
    REVIEW = "review"
    NEEDS_INFO = "needs_info"


class Platform(StrEnum):
    """Target platform for analysis."""
    FRESHDESK = "freshdesk"
    ZENDESK = "zendesk"
    INTERCOM = "intercom"
    WEB = "web"


class ReviewVerdict(StrEnum):
    """Reviewer verdict on draft quality."""
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


# -----------------------------------------------------------------------------
# Shared constants
# -----------------------------------------------------------------------------
CONTRACT_VERSION: str = "v1"


# -----------------------------------------------------------------------------
# Request/Response models for SSE endpoint
# -----------------------------------------------------------------------------


class AXAnalysisRequest(BaseModel):
    """Request to start an analysis job."""
    model_config = ConfigDict(extra="forbid")

    # Core identifiers
    job_id: UUID = Field(description="Unique identifier for this analysis job")
    correlation_id: str = Field(description="Correlation ID for tracing")
    tenant_id: UUID = Field(description="Tenant ID")
    channel_connection_id: Optional[UUID] = Field(
        default=None, description="Channel connection ID"
    )
    engine_binding_id: Optional[UUID] = Field(
        default=None, description="AX Engine binding ID"
    )

    # Target ticket
    platform: Platform = Platform.FRESHDESK
    external_ticket_id: str = Field(description="External ticket ID")
    external_ticket_display_id: Optional[str] = Field(
        default=None, description="Display ticket ID (e.g., #1234)"
    )

    # Trigger/delivery mode (modal-first defaults)
    trigger_source: TriggerSource = TriggerSource.MODAL
    delivery_mode: DeliveryMode = DeliveryMode.STREAM_ONLY

    # Ticket data
    ticket_data: dict = Field(
        default_factory=dict,
        description="Ticket data (subject, description, requester, etc.)"
    )

    # Settings
    settings: dict = Field(
        default_factory=dict,
        description="Analysis settings (model, language, write_private_note, etc.)"
    )

    # Audit
    request_timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When the request was created"
    )


class AXStreamEvent(BaseModel):
    """SSE stream event from Hermes to FastAPI."""
    model_config = ConfigDict(extra="forbid")

    job_id: UUID = Field(description="Job ID this event belongs to")
    stage: StreamStage = Field(description="Current pipeline stage")
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When this event was generated"
    )
    progress: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Progress within current stage (0.0-1.0)"
    )
    message: Optional[str] = Field(
        default=None,
        description="Human-readable status update"
    )
    data: Optional[dict] = Field(
        default=None,
        description="Stage-specific data payload"
    )


class AXAnalysisResult(BaseModel):
    """Final analysis result from Hermes."""
    model_config = ConfigDict(extra="forbid")

    job_id: UUID = Field(description="Job ID")
    tenant_id: UUID = Field(description="Tenant ID")
    status: JobStatus = Field(description="Final job status")
    completed_at: datetime = Field(description="When analysis completed")

    # Analysis outputs
    analysis_summary: Optional[dict] = Field(
        default=None,
        description="Analyzer output summary"
    )
    draft_output: Optional[dict] = Field(
        default=None,
        description="Drafter output"
    )
    review_output: Optional[dict] = Field(
        default=None,
        description="Reviewer output"
    )

    # Final outputs for write-back
    final_private_note_html: Optional[str] = Field(
        default=None,
        description="HTML for private note write-back"
    )
    final_private_note_text: Optional[str] = Field(
        default=None,
        description="Plain text version of private note"
    )
    suggested_status_change: Optional[str] = Field(
        default=None,
        description="Suggested ticket status change"
    )
    suggested_tags: list[str] = Field(
        default_factory=list,
        description="Suggested tags to apply"
    )

    # Quality/confidence
    confidence_score: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Overall confidence score"
    )
    recommendation: Recommendation = Recommendation.REVIEW

    # Usage tracking
    tokens_input: Optional[int] = Field(
        default=None,
        ge=0,
        description="Input tokens consumed"
    )
    tokens_output: Optional[int] = Field(
        default=None,
        ge=0,
        description="Output tokens generated"
    )
    model_name: Optional[str] = Field(
        default=None,
        description="LLM model used"
    )
    latency_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Total processing latency in ms"
    )

    # Agent feedback (populated later)
    agent_feedback: Optional[dict] = Field(
        default=None,
        description="Agent feedback on the analysis"
    )

    @model_validator(mode="after")
    def validate_status_outputs(self) -> AXAnalysisResult:
        """Validate that completed jobs have required outputs."""
        if self.status == JobStatus.COMPLETED:
            if self.analysis_summary is None:
                raise ValueError("completed jobs must have analysis_summary")
        elif self.status == JobStatus.FAILED:
            if self.analysis_summary is not None:
                raise ValueError("failed jobs should not have analysis_summary")
        return self


class AXAnalysisError(BaseModel):
    """Error information from Hermes."""
    model_config = ConfigDict(extra="forbid")

    job_id: UUID = Field(description="Job ID")
    stage: StreamStage = Field(description="Stage where error occurred")
    error_code: str = Field(description="Error code")
    message: str = Field(description="Error message")
    detail: Optional[dict] = Field(
        default=None,
        description="Additional error details"
    )
    should_retry: bool = Field(
        default=False,
        description="Whether the job should be retried"
    )
    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="When error occurred"
    )


# Backwards-compatible alias for AXE-20/Hermes bridge wording.
AXErrorInfo = AXAnalysisError


# -----------------------------------------------------------------------------
# Hermes → FastAPI adapter models
# -----------------------------------------------------------------------------


class HermesAnalysisRequest(BaseModel):
    """Request format that Hermes expects."""
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(description="Job ID as string")
    ticket_data: dict = Field(description="Ticket data")
    settings: dict = Field(description="Analysis settings")
    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata for Hermes"
    )


class HermesAnalysisResponse(BaseModel):
    """Response format from Hermes."""
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(description="Job ID")
    status: Literal["completed", "failed"] = Field(description="Final status")
    outputs: Optional[dict] = Field(
        default=None,
        description="Analysis outputs"
    )
    error: Optional[dict] = Field(
        default=None,
        description="Error information"
    )
    usage: Optional[dict] = Field(
        default=None,
        description="Usage metrics"
    )


# -----------------------------------------------------------------------------
# FastAPI ↔ FDK models
# -----------------------------------------------------------------------------


class ModalAnalysisRequest(BaseModel):
    """Request from FDK modal to FastAPI.

    Modal-first invariants (AXE-19):
    - ``ticket_data`` is inline and required so the modal SSE gateway never
      blocks on a server-side Freshdesk fetch.
    - ``trigger_source`` must be ``modal``; ``scheduled`` / ``admin_manual``
      must use a different surface.
    - ``delivery_mode`` must be ``stream_only`` or ``stream_and_note``;
      ``note_only`` is rejected because the modal owns the user-visible stream.
    """
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID = Field(description="Tenant ID")
    ticket_id: str = Field(description="External ticket ID")
    ticket_display_id: Optional[str] = Field(
        default=None,
        description="Display ticket ID (e.g., #1234)"
    )
    platform: Platform = Platform.FRESHDESK
    trigger_source: TriggerSource = TriggerSource.MODAL
    delivery_mode: DeliveryMode = DeliveryMode.STREAM_ONLY

    # FDK ships the ticket payload inline so the gateway never re-fetches
    # Freshdesk during the modal stream.
    ticket_data: dict = Field(
        description="Inline ticket payload (subject, description, requester, ...)"
    )

    # Optional analysis settings (model, language, ...) and trace correlation.
    settings: dict = Field(
        default_factory=dict,
        description="Analysis settings forwarded to the AX engine"
    )
    correlation_id: Optional[str] = Field(
        default=None,
        description="Optional client-supplied correlation id"
    )
    channel_connection_id: Optional[UUID] = Field(
        default=None,
        description="Channel connection ID (Freshdesk install)"
    )
    engine_binding_id: Optional[UUID] = Field(
        default=None,
        description="AX Engine binding ID"
    )

    @model_validator(mode="after")
    def _enforce_modal_invariants(self) -> "ModalAnalysisRequest":
        if self.trigger_source != TriggerSource.MODAL:
            raise ValueError(
                "modal route only accepts trigger_source=modal"
            )
        if self.delivery_mode == DeliveryMode.NOTE_ONLY:
            raise ValueError(
                "modal route rejects delivery_mode=note_only"
            )
        if not self.ticket_data:
            raise ValueError("ticket_data is required for modal requests")
        return self


# -----------------------------------------------------------------------------
# Export
# -----------------------------------------------------------------------------
__all__ = [
    # Enums
    "TriggerSource",
    "DeliveryMode",
    "JobStatus",
    "WritebackStatus",
    "StreamStage",
    "Recommendation",
    "Platform",
    "ReviewVerdict",

    # Constants
    "CONTRACT_VERSION",

    # Core wire models
    "AXAnalysisRequest",
    "AXStreamEvent",
    "AXAnalysisResult",
    "AXAnalysisError",
    "AXErrorInfo",

    # Hermes adapter models
    "HermesAnalysisRequest",
    "HermesAnalysisResponse",

    # FDK models
    "ModalAnalysisRequest",
]
