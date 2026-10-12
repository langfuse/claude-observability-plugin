from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest


def test_read_new_jsonl_reads_once_then_advances_offset(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("simple_turn")
    state = hook_module.SessionState()

    rows, state = hook_module.read_new_jsonl(transcript, state)
    assert len(rows) == 3
    assert state.offset == transcript.stat().st_size
    assert state.buffer == ""

    rows, state = hook_module.read_new_jsonl(transcript, state)
    assert rows == []
    assert state.offset == transcript.stat().st_size


def test_read_new_jsonl_preserves_partial_line_between_reads(hook_module, tmp_path):
    transcript = tmp_path / "partial.jsonl"
    first_row = {"type": "user", "message": {"role": "user", "content": "hello"}}
    second_row = {"type": "assistant", "message": {"role": "assistant", "content": []}}
    transcript.write_text(json.dumps(first_row), encoding="utf-8")

    state = hook_module.SessionState()
    rows, state = hook_module.read_new_jsonl(transcript, state)
    assert rows == []
    assert state.buffer == json.dumps(first_row)

    with transcript.open("a", encoding="utf-8") as fh:
        fh.write("\n")
        fh.write(json.dumps(second_row))
        fh.write("\n")

    rows, state = hook_module.read_new_jsonl(transcript, state)
    assert rows == [first_row, second_row]
    assert state.buffer == ""


def test_read_new_jsonl_restarts_when_file_shrinks(hook_module, tmp_path):
    transcript = tmp_path / "rotated.jsonl"
    first = {"type": "user", "message": {"role": "user", "content": "old transcript content"}}
    second = {"type": "user", "message": {"role": "user", "content": "new"}}
    transcript.write_text(json.dumps(first) + "\n", encoding="utf-8")

    state = hook_module.SessionState()
    rows, state = hook_module.read_new_jsonl(transcript, state)
    assert rows == [first]

    transcript.write_text(json.dumps(second) + "\n", encoding="utf-8")
    rows, state = hook_module.read_new_jsonl(transcript, state)
    assert rows == [second]
    assert state.offset == transcript.stat().st_size


def test_rotation_clears_persisted_turn_state_and_avoids_duplicate_turns(hook_module, tmp_path):
    """Regression (LFE-10752): when the transcript shrinks (rotation), the
    offset restarts at 0 but held turn state used to survive — re-reading the
    file then emitted the held turn a second time."""
    import json

    transcript = tmp_path / "transcript.jsonl"

    def turn_rows(name: str) -> list[dict]:
        return [
            {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": f"{name}-user",
             "message": {"role": "user", "content": "Question."}},
            {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "uuid": f"{name}-assistant",
             "message": {"id": f"{name}-msg", "role": "assistant", "model": "m",
                         "content": [{"type": "text", "text": "Answer."}]}},
        ]

    def write(rows: list[dict]) -> None:
        transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    emitted = []
    state = hook_module.SessionState()

    # Run 1: two separate turns; turn A closes, turn B is trailing -> held open.
    write(turn_rows("turn-a") + turn_rows("turn-b"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted += hook_module.get_turns_to_emit(turns, state)
    assert [t.user_msg["uuid"] for t in emitted] == ["turn-a-user"]
    assert state.open_turn.get("rows")
    assert state.offset > 0

    # Rotation: the file is replaced by a SHORTER one that still contains the
    # held turn's rows (rewrite-after-truncate shape).
    write(turn_rows("turn-b"))
    turns, state = hook_module.get_new_turns_from_transcript(
        transcript, state, flush_deferred_agent_turns=True)
    emitted += hook_module.get_turns_to_emit(turns, state, flush_deferred_agent_turns=True)

    # Across BOTH runs every turn is emitted exactly once — the held copy of
    # turn B must not be emitted alongside the re-read one.
    assert [t.user_msg["uuid"] for t in emitted] == ["turn-a-user", "turn-b-user"]
    assert state.pending_agent_turns == []
    assert state.pending_task_notifications == []
    assert state.open_turn == {}


# ----------------- Session title -----------------

TITLED_SESSION_ID = "session-titled"


def custom_title_row(value: object) -> dict:
    return {"type": "custom-title", "customTitle": value, "sessionId": TITLED_SESSION_ID}


def ai_title_row(value: object) -> dict:
    return {"type": "ai-title", "aiTitle": value, "sessionId": TITLED_SESSION_ID}


def title_of(hook_module, rows: list[dict]) -> str | None:
    state = hook_module.SessionState()
    hook_module.update_session_titles(state, rows)
    return hook_module.get_session_title(state)


def conversation_rows(name: str) -> list[dict]:
    return [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": f"{name}-user",
         "sessionId": TITLED_SESSION_ID, "message": {"role": "user", "content": "Question."}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "uuid": f"{name}-assistant",
         "sessionId": TITLED_SESSION_ID,
         "message": {"id": f"{name}-msg", "role": "assistant", "model": "m",
                     "content": [{"type": "text", "text": "Answer."}]}},
    ]


def write_transcript(directory: Path, rows: list[dict]) -> Path:
    transcript = directory / f"{TITLED_SESSION_ID}.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return transcript


def record_propagated_trace_names(hook_module, monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    names: list[str | None] = []

    @contextlib.contextmanager
    def recording_propagate(**kwargs):
        names.append(kwargs.get("trace_name"))
        yield

    monkeypatch.setattr(hook_module, "propagate_attributes", recording_propagate)
    return names


def test_a_custom_title_wins_over_an_ai_title_whichever_row_came_last(hook_module):
    assert title_of(hook_module, [
        custom_title_row("Meeting Module"),
        ai_title_row("Langfuse port 5432 conflict"),
    ]) == "Meeting Module"
    assert title_of(hook_module, [
        ai_title_row("Langfuse port 5432 conflict"),
        custom_title_row("Meeting Module"),
    ]) == "Meeting Module"


def test_the_last_title_row_of_a_type_replaces_the_earlier_one(hook_module):
    assert title_of(hook_module, [
        custom_title_row("First name"),
        custom_title_row("Second name"),
    ]) == "Second name"
    assert title_of(hook_module, [
        ai_title_row("First guess"),
        ai_title_row("Second guess"),
    ]) == "Second guess"


def test_an_ai_title_is_used_until_the_session_is_renamed(hook_module):
    state = hook_module.SessionState()
    hook_module.update_session_titles(state, [ai_title_row("Langfuse port 5432 conflict")])
    assert hook_module.get_session_title(state) == "Langfuse port 5432 conflict"

    hook_module.update_session_titles(state, [custom_title_row("Meeting Module")])
    assert hook_module.get_session_title(state) == "Meeting Module"


def test_unusable_title_values_are_ignored(hook_module):
    assert title_of(hook_module, [custom_title_row("")]) is None
    assert title_of(hook_module, [custom_title_row("   ")]) is None
    assert title_of(hook_module, [custom_title_row(None)]) is None
    assert title_of(hook_module, [custom_title_row(42)]) is None
    assert title_of(hook_module, [{"type": "custom-title", "sessionId": TITLED_SESSION_ID}]) is None
    assert title_of(hook_module, [{"type": "user", "customTitle": "Not a title row"}]) is None

    # A usable earlier title is kept, not replaced by the unusable one.
    assert title_of(hook_module, [
        custom_title_row("Meeting Module"),
        custom_title_row("  "),
    ]) == "Meeting Module"


def test_a_title_is_trimmed_and_capped(hook_module):
    assert title_of(hook_module, [custom_title_row("  Meeting Module \n")]) == "Meeting Module"

    capped = title_of(hook_module, [custom_title_row("x" * 5000)])
    assert capped == "x" * hook_module.MAX_SESSION_TITLE_CHARS
    assert len(capped) == 200


def test_titles_round_trip_through_the_state_file_representation(hook_module):
    state = hook_module.SessionState()
    hook_module.update_session_titles(state, [
        ai_title_row("Langfuse port 5432 conflict"),
        custom_title_row("Meeting Module"),
    ])

    global_state: dict = {}
    hook_module.update_session_state(global_state, "key", state)
    assert global_state["key"]["titles_by_type"] == {
        "ai-title": "Langfuse port 5432 conflict",
        "custom-title": "Meeting Module",
    }

    restored = hook_module.get_session_state(global_state, "key")
    assert hook_module.get_session_title(restored) == "Meeting Module"


def test_the_session_title_is_the_trace_name_and_outlives_the_firing_that_saw_it(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    transcript = write_transcript(
        tmp_path,
        conversation_rows("turn-a") + [custom_title_row("Meeting Module")] + conversation_rows("turn-b"),
    )
    trace_names = record_propagated_trace_names(hook_module, monkeypatch)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    assert hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, TITLED_SESSION_ID, transcript) == 1

    # Second firing: the title row sits behind the read offset, so only the
    # title persisted in session state can name the trace.
    assert hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, TITLED_SESSION_ID, transcript,
        flush_deferred_agent_turns=True) == 1

    assert len(trace_names) >= 2
    assert set(trace_names) == {"Meeting Module"}


def test_the_trace_name_falls_back_to_the_constant_without_a_title_row(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    transcript = write_transcript(tmp_path, conversation_rows("turn-a") + conversation_rows("turn-b"))
    trace_names = record_propagated_trace_names(hook_module, monkeypatch)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, TITLED_SESSION_ID, transcript)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, TITLED_SESSION_ID, transcript, flush_deferred_agent_turns=True)

    assert trace_names
    assert set(trace_names) == {hook_module.TRACE_NAME}
