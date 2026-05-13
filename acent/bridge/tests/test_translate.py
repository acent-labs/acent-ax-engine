"""StageTranslator unit tests.

Acceptance criteria coverage (from AXE-20):
* ACP update → AXStreamEvent translation
* sequence monotonicity (FETCHING → ANALYZING → DRAFTING → REVIEWING)
"""
from __future__ import annotations

from uuid import UUID

import pytest

from ax_bridge.contract import StreamStage
from ax_bridge.translate import StageTranslator


JOB_ID = UUID("00000000-0000-0000-0000-00000000aa01")


@pytest.fixture
def translator() -> StageTranslator:
    return StageTranslator(JOB_ID)


def _kanban_call(title: str, *, status: str = "in_progress") -> dict:
    return {
        "sessionUpdate": "tool_call",
        "toolCallId": f"tc-{title}",
        "title": title,
        "kind": "other",
        "status": status,
    }


def test_initial_stage_is_fetching(translator: StageTranslator) -> None:
    assert translator.current_stage == StreamStage.FETCHING
    assert translator.stage_history == [StreamStage.FETCHING]


def test_agent_message_chunk_is_dropped_at_bridge_boundary(translator: StageTranslator) -> None:
    # ACP streams LLM output token-by-token. Forwarding one event per token
    # repaints the FDK modal per character ("flickering single-letter" UX
    # seen in AXE-20 dogfooding). Stage progress is delivered via tool_call
    # transitions instead; chunk content is dropped at the bridge boundary.
    assert translator.translate(
        {
            "sessionUpdate": "agent_message_chunk",
            "content": {"type": "text", "text": "fetching ticket"},
        }
    ) is None
    assert translator.translate({"sessionUpdate": "agent_message_chunk"}) is None


def test_kanban_analyzer_advances_to_analyzing(translator: StageTranslator) -> None:
    event = translator.translate(_kanban_call("ax-analyzer task"))
    assert event is not None
    assert event.stage == StreamStage.ANALYZING
    assert translator.current_stage == StreamStage.ANALYZING
    assert translator.stage_history == [StreamStage.FETCHING, StreamStage.ANALYZING]


def test_kanban_progression_is_monotonic(translator: StageTranslator) -> None:
    translator.translate(_kanban_call("ax-analyzer"))
    translator.translate(_kanban_call("ax-drafter"))
    translator.translate(_kanban_call("ax-reviewer"))
    assert translator.current_stage == StreamStage.REVIEWING
    assert translator.stage_history == [
        StreamStage.FETCHING,
        StreamStage.ANALYZING,
        StreamStage.DRAFTING,
        StreamStage.REVIEWING,
    ]


def test_kanban_regression_does_not_rewind(translator: StageTranslator) -> None:
    translator.translate(_kanban_call("ax-drafter"))
    assert translator.current_stage == StreamStage.DRAFTING
    # Late-arriving analyzer update must not regress.
    event = translator.translate(_kanban_call("ax-analyzer"))
    assert event is not None
    assert event.stage == StreamStage.DRAFTING
    assert translator.current_stage == StreamStage.DRAFTING
    assert StreamStage.ANALYZING not in translator.stage_history


def test_kanban_via_raw_input_task_field(translator: StageTranslator) -> None:
    event = translator.translate(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tc-1",
            "title": "kanban: create task",
            "rawInput": {"task": "ax-drafter"},
        }
    )
    assert event is not None
    assert event.stage == StreamStage.DRAFTING


def test_unknown_tool_call_returns_none(translator: StageTranslator) -> None:
    event = translator.translate(
        {"sessionUpdate": "tool_call", "toolCallId": "x", "title": "Read file foo.py"}
    )
    assert event is None


def test_plan_update_emits_data_payload(translator: StageTranslator) -> None:
    entries = [{"content": "step 1", "status": "pending"}]
    event = translator.translate({"sessionUpdate": "plan", "entries": entries})
    assert event is not None
    assert event.data == {"plan_entries": entries}
    assert event.stage == StreamStage.FETCHING


def test_agent_thought_chunk_is_dropped(translator: StageTranslator) -> None:
    assert (
        translator.translate(
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": "reasoning..."},
            }
        )
        is None
    )


def test_unknown_session_update_kind_is_dropped(translator: StageTranslator) -> None:
    assert translator.translate({"sessionUpdate": "something_new"}) is None
