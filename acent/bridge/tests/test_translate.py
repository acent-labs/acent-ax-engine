"""Unit tests for the ACP→AXStreamEvent translator.

No Hermes / ACP runtime needed — translator is a pure function over
plain dict ``SessionNotification.update`` payloads.
"""

from __future__ import annotations

from ax_bridge.contract import StreamStage
from ax_bridge.translate import (
    new_context,
    translate_session_update,
    translate_stream,
)


def _msg_chunk(text: str) -> dict:
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


def _kanban_tool_call(profile: str) -> dict:
    return {
        "sessionUpdate": "tool_call",
        "kind": "kanban_create",
        "args": {"profile": profile, "title": f"task for {profile}"},
    }


def test_first_event_is_accepted():
    ctx = new_context("run-1")
    events = list(translate_session_update(_msg_chunk("hello"), ctx))
    assert events[0].stage is StreamStage.ACCEPTED
    assert events[0].sequence == 1
    assert events[0].run_id == "run-1"


def test_kanban_task_creation_drives_stage_transition():
    events = list(
        translate_stream(
            [
                _msg_chunk("planning"),
                _kanban_tool_call("ax-analyzer"),
                _msg_chunk("classifying intent"),
                _kanban_tool_call("ax-drafter"),
                _msg_chunk("writing note"),
                _kanban_tool_call("ax-reviewer"),
                _msg_chunk("verifying"),
            ],
            run_id="run-2",
        )
    )

    stages = [e.stage for e in events]
    # ACCEPTED is emitted exactly once, at the very start.
    assert stages[0] is StreamStage.ACCEPTED
    assert stages.count(StreamStage.ACCEPTED) == 1

    # Each kanban tool call flips to the matching stage.
    assert StreamStage.ANALYZING in stages
    assert StreamStage.DRAFTING in stages
    assert StreamStage.REVIEWING in stages

    # Stage order matches kanban dispatch order.
    first_idx = {
        s: stages.index(s)
        for s in (StreamStage.ANALYZING, StreamStage.DRAFTING, StreamStage.REVIEWING)
    }
    assert first_idx[StreamStage.ANALYZING] < first_idx[StreamStage.DRAFTING]
    assert first_idx[StreamStage.DRAFTING] < first_idx[StreamStage.REVIEWING]


def test_unknown_profile_does_not_change_stage():
    ctx = new_context("run-3")
    list(translate_session_update(_msg_chunk("warm-up"), ctx))  # ACCEPTED + chunk
    list(translate_session_update(_kanban_tool_call("ax-analyzer"), ctx))
    assert ctx.current_stage is StreamStage.ANALYZING

    # An unknown profile should NOT flip the stage.
    list(translate_session_update(_kanban_tool_call("random-helper"), ctx))
    assert ctx.current_stage is StreamStage.ANALYZING


def test_sequence_is_monotonic():
    events = list(translate_stream(
        [_msg_chunk(f"chunk {i}") for i in range(5)],
        run_id="run-4",
    ))
    seqs = [e.sequence for e in events]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1
    assert len(set(seqs)) == len(seqs)


def test_chunk_payload_carries_acp_update_verbatim():
    ctx = new_context("run-5")
    events = list(translate_session_update(_msg_chunk("hello"), ctx))
    # First event is ACCEPTED with empty payload; second is the actual chunk
    chunk_event = events[1]
    assert chunk_event.stage is StreamStage.ANALYZING  # default current
    assert chunk_event.payload["acp_update"]["sessionUpdate"] == "agent_message_chunk"
    assert chunk_event.payload["acp_update"]["content"]["text"] == "hello"


def test_stage_transition_event_has_transition_marker():
    ctx = new_context("run-6")
    list(translate_session_update(_msg_chunk("warm"), ctx))  # ACCEPTED + chunk
    transitions = list(translate_session_update(_kanban_tool_call("ax-drafter"), ctx))
    transition_event = next(e for e in transitions if e.stage is StreamStage.DRAFTING)
    assert transition_event.payload.get("transition") is True
