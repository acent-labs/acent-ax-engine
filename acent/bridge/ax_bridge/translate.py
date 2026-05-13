"""ACP session_update → AXStreamEvent translation.

The Hermes ACP server (Zed Agent Client Protocol) emits `session/update`
notifications during a prompt turn. This module turns those updates into
the AXStreamEvent envelope the FastAPI gateway forwards to the FDK modal.

The translator is stateful: it remembers the current pipeline stage so it
can enforce **monotonic stage progression** (FETCHING → ANALYZING → DRAFTING
→ REVIEWING → COMPLETED). Updates that would regress are dropped.

The translator does NOT emit the leading ACCEPTED or trailing COMPLETED/ERROR
event itself — those are synthesized by the session driver around the
prompt turn boundary.
"""
from __future__ import annotations

from typing import Optional
from uuid import UUID

from .contract import AXStreamEvent, StreamStage


# Map a kanban task title (or rawInput["task"]) to the AX pipeline stage.
KANBAN_STAGE_MAP: dict[str, StreamStage] = {
    "ax-analyzer": StreamStage.ANALYZING,
    "ax-drafter": StreamStage.DRAFTING,
    "ax-reviewer": StreamStage.REVIEWING,
}

# Monotonic ordering used to suppress stage regressions.
STAGE_ORDER: list[StreamStage] = [
    StreamStage.ACCEPTED,
    StreamStage.FETCHING,
    StreamStage.ANALYZING,
    StreamStage.DRAFTING,
    StreamStage.REVIEWING,
    StreamStage.COMPLETED,
]


def _stage_index(stage: StreamStage) -> int:
    try:
        return STAGE_ORDER.index(stage)
    except ValueError:
        return -1


def _kanban_stage_from_update(update: dict) -> Optional[StreamStage]:
    """Pick the AX stage hinted at by an ACP tool_call/tool_call_update."""
    title = (update.get("title") or "").strip().lower()
    for key, stage in KANBAN_STAGE_MAP.items():
        if key in title:
            return stage
    raw = update.get("rawInput")
    if isinstance(raw, dict):
        for hint_key in ("task", "task_name", "name"):
            v = raw.get(hint_key)
            if isinstance(v, str):
                normalized = v.strip().lower()
                for key, stage in KANBAN_STAGE_MAP.items():
                    if key in normalized:
                        return stage
    return None


def _text_from_content(content: object) -> Optional[str]:
    """Extract a text string from an ACP content block (best-effort)."""
    if isinstance(content, dict):
        if content.get("type") == "text":
            text = content.get("text")
            if isinstance(text, str):
                return text
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            extracted = _text_from_content(item)
            if extracted:
                parts.append(extracted)
        if parts:
            return "".join(parts)
    return None


class StageTranslator:
    """Stateful translator with monotonic stage enforcement."""

    def __init__(self, job_id: UUID, *, initial_stage: StreamStage = StreamStage.FETCHING) -> None:
        self.job_id = job_id
        self.current_stage: StreamStage = initial_stage
        self.stage_history: list[StreamStage] = [initial_stage]

    def _advance_to(self, stage: StreamStage) -> bool:
        """Try to advance to `stage`. Returns True if state actually moved."""
        if _stage_index(stage) <= _stage_index(self.current_stage):
            return False
        self.current_stage = stage
        self.stage_history.append(stage)
        return True

    def translate(self, update: dict) -> Optional[AXStreamEvent]:
        """Convert one ACP session_update dict into an AXStreamEvent.

        Returns None if the update does not produce an outgoing event.
        Recognized shapes (other shapes are silently dropped):

        * ``agent_message_chunk`` — emits a status update at the current stage
        * ``tool_call`` / ``tool_call_update`` — kanban task transitions advance
          the stage to ANALYZING / DRAFTING / REVIEWING
        * ``plan`` — emits a structured-progress update at the current stage
        """
        kind = update.get("sessionUpdate")
        if kind in ("tool_call", "tool_call_update"):
            stage = _kanban_stage_from_update(update)
            if stage is None:
                return None
            advanced = self._advance_to(stage)
            if not advanced:
                # Same-or-earlier stage — surface a status ping at current stage.
                return AXStreamEvent(
                    job_id=self.job_id,
                    stage=self.current_stage,
                    message=update.get("title"),
                )
            return AXStreamEvent(
                job_id=self.job_id,
                stage=self.current_stage,
                message=update.get("title"),
                data={
                    "tool_call_id": update.get("toolCallId"),
                    "status": update.get("status"),
                },
            )

        if kind == "agent_message_chunk":
            # ACP streams LLM output token-by-token. Each chunk carries a
            # tiny fragment (e.g. "결", "제", " 실패"); forwarding one
            # AXStreamEvent per chunk forces the FDK modal to repaint
            # per-token and produces the "flickering single character"
            # UX seen in AXE-20 dogfooding. Stage progress is already
            # surfaced via tool_call kanban transitions, so token-level
            # text is dropped at the bridge boundary.
            return None

        if kind == "agent_thought_chunk":
            # Internal reasoning — not surfaced to FDK.
            return None

        if kind == "plan":
            entries = update.get("entries")
            if not isinstance(entries, list):
                return None
            return AXStreamEvent(
                job_id=self.job_id,
                stage=self.current_stage,
                data={"plan_entries": entries},
            )

        return None


__all__ = ["StageTranslator", "KANBAN_STAGE_MAP", "STAGE_ORDER"]
