"""ACP session-update → AXStreamEvent translation.

Pure functions only — no I/O. The bridge consumer feeds ACP
``SessionNotification`` payloads here and gets back a stream of
:class:`AXStreamEvent` ready to be SSE-encoded.

Stage inference rules
---------------------

ACP delivers fine-grained chunks (text, thoughts, tool calls, plan updates)
that are richer than the modal-first ``StreamStage`` enum. We collapse them
into the five visible stages the contract defines:

* ``ACCEPTED``   — emitted once, on the very first translated event
* ``ANALYZING``  — agent is in the analyzer phase (heuristic: tool call to
                   the ``ax-analyzer`` profile, or thought/message before
                   the drafter starts)
* ``DRAFTING``   — drafter profile active
* ``REVIEWING``  — reviewer profile active
* ``COMPLETED``  — terminal; emitted by the session driver, not here
* ``ERROR``      — terminal; emitted by the session driver on failure

The translator only emits the non-terminal stages (ACCEPTED through
REVIEWING). The driver wraps a terminal event around the stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping

from ax_bridge.contract import AXStreamEvent, StreamStage


# ---------------------------------------------------------------------------
# Stage routing — heuristic for now; tightened once we observe real Hermes
# transcripts running the acent-ax-analysis skill.
# ---------------------------------------------------------------------------


_PROFILE_TO_STAGE: Mapping[str, StreamStage] = {
    "ax-analyzer": StreamStage.ANALYZING,
    "ax-drafter": StreamStage.DRAFTING,
    "ax-reviewer": StreamStage.REVIEWING,
    # Reasonable fallbacks if the orchestrator names workers differently:
    "analyzer": StreamStage.ANALYZING,
    "drafter": StreamStage.DRAFTING,
    "reviewer": StreamStage.REVIEWING,
}


@dataclass
class _StageContext:
    """Mutable state carried across calls within a single run."""

    run_id: str
    sequence: int = 0
    accepted_emitted: bool = False
    current_stage: StreamStage = StreamStage.ANALYZING
    stage_history: list[StreamStage] = field(default_factory=list)

    def next_seq(self) -> int:
        self.sequence += 1
        return self.sequence


def new_context(run_id: str) -> _StageContext:
    """Create a fresh translator context for one analysis run."""
    return _StageContext(run_id=run_id)


# ---------------------------------------------------------------------------
# ACP update inspection
# ---------------------------------------------------------------------------


def _profile_from_kanban_task(update_payload: Mapping[str, Any]) -> str | None:
    """Extract a worker profile name from a kanban-task tool call payload.

    The kanban orchestrator skill creates tasks with ``profile`` (e.g.
    ``ax-analyzer``) in the tool-call arguments. We don't strictly bind
    to the ACP schema here — accept any nested ``profile`` key.
    """

    def _walk(value: Any) -> str | None:
        if isinstance(value, Mapping):
            if "profile" in value and isinstance(value["profile"], str):
                return value["profile"]
            for v in value.values():
                found = _walk(v)
                if found:
                    return found
        elif isinstance(value, (list, tuple)):
            for v in value:
                found = _walk(v)
                if found:
                    return found
        return None

    return _walk(update_payload)


def _infer_stage_from_update(update: Mapping[str, Any]) -> StreamStage | None:
    """Read an ACP ``SessionNotification.update`` and decide a stage hint.

    Returns ``None`` when the update doesn't change stage (e.g. a generic
    text chunk in the middle of a stage).
    """

    update_type = update.get("sessionUpdate") or update.get("session_update")
    # Tool-call starts that look like kanban task creation → stage switch.
    if update_type in ("tool_call", "toolCall", "tool_call_start", "toolCallStart"):
        profile = _profile_from_kanban_task(update)
        if profile and profile in _PROFILE_TO_STAGE:
            return _PROFILE_TO_STAGE[profile]
    return None


# ---------------------------------------------------------------------------
# Public translation API
# ---------------------------------------------------------------------------


def translate_session_update(
    update: Mapping[str, Any],
    ctx: _StageContext,
) -> Iterator[AXStreamEvent]:
    """Yield zero or more AXStreamEvent from one ACP session update.

    The first call always emits an ``ACCEPTED`` event before anything
    else (mirroring the contract's required terminal-bookend shape).
    Stage transitions emit a single event with the new stage; chunk
    updates carry the chunk payload tagged with the current stage.
    """

    if not ctx.accepted_emitted:
        ctx.accepted_emitted = True
        yield AXStreamEvent(
            run_id=ctx.run_id,
            sequence=ctx.next_seq(),
            stage=StreamStage.ACCEPTED,
            payload={},
        )

    inferred = _infer_stage_from_update(update)
    if inferred is not None and inferred != ctx.current_stage:
        ctx.current_stage = inferred
        ctx.stage_history.append(inferred)
        yield AXStreamEvent(
            run_id=ctx.run_id,
            sequence=ctx.next_seq(),
            stage=inferred,
            payload={"transition": True},
        )
        return

    # Non-stage-transition update → forward as a payload chunk on the
    # current stage. Keeping the FDK informed of progress without
    # requiring it to understand ACP's full schema.
    yield AXStreamEvent(
        run_id=ctx.run_id,
        sequence=ctx.next_seq(),
        stage=ctx.current_stage,
        payload={"acp_update": dict(update)},
    )


def translate_stream(
    updates: Iterable[Mapping[str, Any]],
    *,
    run_id: str,
) -> Iterator[AXStreamEvent]:
    """Convenience: drive :func:`translate_session_update` over an iterable."""

    ctx = new_context(run_id)
    for update in updates:
        yield from translate_session_update(update, ctx)


__all__ = [
    "translate_session_update",
    "translate_stream",
    "new_context",
]
