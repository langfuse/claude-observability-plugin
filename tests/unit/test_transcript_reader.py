from __future__ import annotations

import json


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
