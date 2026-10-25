"""Generation inputs after a /compact show the context window the model had.

After a compaction (manual /compact or automatic), Claude Code continues from
the compact summary plus any preserved messages. Messages before the compact
boundary are no longer in the context window, so they must not appear in the
input of later generations.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SESSION = "dddddddd-4444-4444-4444-444444444444"
SUMMARY_TEXT = "This session is being continued from a previous conversation. Summary: ..."


def user_row(index: int, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-08-11T14:{index:02d}:00.000Z",
        "sessionId": SESSION,
        "uuid": f"user-row-{index}",
        "message": {"role": "user", "content": text},
    }


def assistant_row(index: int, text: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": f"2026-08-11T14:{index:02d}:10.000Z",
        "sessionId": SESSION,
        "uuid": f"assistant-row-{index}",
        "message": {
            "id": f"msg-{index}",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 2, "output_tokens": 5},
        },
    }


def compact_boundary_row(index: int, preserved: tuple[str, str] | None = None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"trigger": "manual", "preTokens": 1000}
    if preserved:
        head, tail = preserved
        metadata["preservedSegment"] = {"headUuid": head, "anchorUuid": tail, "tailUuid": tail}
    return {
        "type": "system",
        "subtype": "compact_boundary",
        "content": "Conversation compacted",
        "timestamp": f"2026-08-11T14:{index:02d}:00.000Z",
        "sessionId": SESSION,
        "uuid": f"boundary-{index}",
        "isMeta": False,
        "compactMetadata": metadata,
    }


def summary_row(index: int) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-08-11T14:{index:02d}:01.000Z",
        "sessionId": SESSION,
        "uuid": f"summary-{index}",
        "isCompactSummary": True,
        "isVisibleInTranscriptOnly": True,
        "message": {"role": "user", "content": SUMMARY_TEXT},
    }


def turn(index: int) -> list[dict[str, Any]]:
    return [user_row(index, f"Question {index}?"), assistant_row(index, f"Answer {index}.")]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def emit(hook_module: Any, fake_langfuse: Any, transcript: Path) -> list[Any]:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)
    return [o.kwargs.get("input") for o in fake_langfuse.observations if o.as_type == "generation"]


def contents(messages: list[dict[str, Any]]) -> list[Any]:
    return [m.get("content") for m in messages]


def test_turn_after_compact_sees_summary_not_earlier_turns(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, turn(1) + turn(2) + [compact_boundary_row(3), summary_row(3)] + turn(4))

    inputs = emit(hook_module, fake_langfuse, transcript)

    assert len(inputs) == 3
    # Before the compaction the history is unchanged.
    assert contents(inputs[1]) == ["Question 1?", "Answer 1.", "Question 2?"]
    # After it, the window is the summary and the new turn only.
    assert contents(inputs[2]) == [SUMMARY_TEXT, "Question 4?"]


def test_preserved_segment_follows_the_summary(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows = (
        turn(1) + turn(2)
        + [compact_boundary_row(3, preserved=("user-row-2", "assistant-row-2")), summary_row(3)]
        + turn(4)
    )
    write_rows(transcript, rows)

    inputs = emit(hook_module, fake_langfuse, transcript)

    assert contents(inputs[-1]) == [SUMMARY_TEXT, "Question 2?", "Answer 2.", "Question 4?"]


def test_work_resumed_after_auto_compact_keeps_the_summary_turn(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # An automatic compaction in the middle of work: the summary row is the user
    # row in front of the resumed assistant reply, so it opens a turn itself.
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows = turn(1) + [compact_boundary_row(2), summary_row(2), assistant_row(3, "Resumed.")] + turn(4)
    write_rows(transcript, rows)

    inputs = emit(hook_module, fake_langfuse, transcript)

    assert contents(inputs[-1]) == [SUMMARY_TEXT, "Resumed.", "Question 4?"]


def test_each_compaction_starts_a_new_window(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows = (
        turn(1) + [compact_boundary_row(2), summary_row(2)] + turn(3)
        + [compact_boundary_row(4), summary_row(4)] + turn(5)
    )
    write_rows(transcript, rows)

    inputs = emit(hook_module, fake_langfuse, transcript)

    assert contents(inputs[1]) == [SUMMARY_TEXT, "Question 3?"]
    assert contents(inputs[2]) == [SUMMARY_TEXT, "Question 5?"]
