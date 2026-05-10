"""
Hermes AX Engine wire contract.

Vendored verbatim from `acent-labs/acent-flow:app/services/ax_engine_hermes/contract.py`
(AXE-22). Keep these two copies in lock-step — the bridge is the receiver of the
exact same Pydantic v2 wire models that acent-flow FastAPI emits.

Version: v1
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TriggerSource(StrEnum):
    MODAL = "modal"
    SCHEDULED = "scheduled"
    ADMIN_MANUAL = "admin_manual"


class DeliveryMode(StrEnum):
    STREAM_ONLY = "stream_only"
    NOTE_ONLY = "note_only"
    STREAM_AND_NOTE = "stream_and_note"


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
    SKIPPED = "skipped"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"


class StreamStage(StrEnum):
    ACCEPTED = "accepted"
    FETCHING = "fetching"
    ANALYZING = "analyzing"
    DRAFTING = "drafting"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    ERROR = "error"


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


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


CONTRACT_VERSION: str = "v1"


class AXAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    correlation_id: str
    tenant_id: UUID
    channel_connection_id: Optional[UUID] = None
    engine_binding_id: Optional[UUID] = None

    platform: Platform = Platform.FRESHDESK
    external_ticket_id: str
    external_ticket_display_id: Optional[str] = None

    trigger_source: TriggerSource = TriggerSource.MODAL
    delivery_mode: DeliveryMode = DeliveryMode.STREAM_ONLY

    ticket_data: dict = Field(default_factory=dict)
    settings: dict = Field(default_factory=dict)

    request_timestamp: datetime = Field(default_factory=datetime.now)


class AXStreamEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    stage: StreamStage
    timestamp: datetime = Field(default_factory=datetime.now)
    progress: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    message: Optional[str] = None
    data: Optional[dict] = None


class AXAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    tenant_id: UUID
    status: JobStatus
    completed_at: datetime

    analysis_summary: Optional[dict] = None
    draft_output: Optional[dict] = None
    review_output: Optional[dict] = None

    final_private_note_html: Optional[str] = None
    final_private_note_text: Optional[str] = None
    suggested_status_change: Optional[str] = None
    suggested_tags: list[str] = Field(default_factory=list)

    confidence_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    recommendation: Recommendation = Recommendation.REVIEW

    tokens_input: Optional[int] = Field(default=None, ge=0)
    tokens_output: Optional[int] = Field(default=None, ge=0)
    model_name: Optional[str] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)

    agent_feedback: Optional[dict] = None

    @model_validator(mode="after")
    def validate_status_outputs(self) -> "AXAnalysisResult":
        if self.status == JobStatus.COMPLETED:
            if self.analysis_summary is None:
                raise ValueError("completed jobs must have analysis_summary")
        elif self.status == JobStatus.FAILED:
            if self.analysis_summary is not None:
                raise ValueError("failed jobs should not have analysis_summary")
        return self


class AXAnalysisError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    stage: StreamStage
    error_code: str
    message: str
    detail: Optional[dict] = None
    should_retry: bool = False
    timestamp: datetime = Field(default_factory=datetime.now)


# Backwards-compatible alias for AXE-20/Hermes bridge wording.
AXErrorInfo = AXAnalysisError


class HermesAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    ticket_data: dict
    settings: dict
    metadata: dict = Field(default_factory=dict)


class HermesAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    status: Literal["completed", "failed"]
    outputs: Optional[dict] = None
    error: Optional[dict] = None
    usage: Optional[dict] = None


class ModalAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    ticket_id: str
    ticket_display_id: Optional[str] = None
    platform: Platform = Platform.FRESHDESK
    trigger_source: TriggerSource = TriggerSource.MODAL
    delivery_mode: DeliveryMode = DeliveryMode.STREAM_ONLY

    ticket_data: dict = Field(description="Inline ticket payload")

    settings: dict = Field(default_factory=dict)
    correlation_id: Optional[str] = None
    channel_connection_id: Optional[UUID] = None
    engine_binding_id: Optional[UUID] = None

    @model_validator(mode="after")
    def _enforce_modal_invariants(self) -> "ModalAnalysisRequest":
        if self.trigger_source != TriggerSource.MODAL:
            raise ValueError("modal route only accepts trigger_source=modal")
        if self.delivery_mode == DeliveryMode.NOTE_ONLY:
            raise ValueError("modal route rejects delivery_mode=note_only")
        if not self.ticket_data:
            raise ValueError("ticket_data is required for modal requests")
        return self


__all__ = [
    "TriggerSource",
    "DeliveryMode",
    "JobStatus",
    "WritebackStatus",
    "StreamStage",
    "Recommendation",
    "Platform",
    "ReviewVerdict",
    "CONTRACT_VERSION",
    "AXAnalysisRequest",
    "AXStreamEvent",
    "AXAnalysisResult",
    "AXAnalysisError",
    "AXErrorInfo",
    "HermesAnalysisRequest",
    "HermesAnalysisResponse",
    "ModalAnalysisRequest",
]
