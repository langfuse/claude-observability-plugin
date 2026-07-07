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
