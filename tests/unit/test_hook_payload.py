from __future__ import annotations

import io
import sys


def test_read_hook_payload_accepts_json_object(hook_module, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"sessionId":"s1"}'))

    assert hook_module.read_hook_payload() == {"sessionId": "s1"}


def test_read_hook_payload_rejects_non_object(hook_module, monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('["not", "an", "object"]'))

    assert hook_module.read_hook_payload() == {}


def test_extract_session_id_and_transcript_path_supports_known_payload_shapes(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    session_id, transcript_path = hook_module.extract_session_id_and_transcript_path(
        {"sessionId": "camel", "transcriptPath": str(transcript)}
    )
    assert session_id == "camel"
    assert transcript_path == transcript.resolve()

    session_id, transcript_path = hook_module.extract_session_id_and_transcript_path(
        {"session_id": "snake", "transcript_path": str(transcript)}
    )
    assert session_id == "snake"
    assert transcript_path == transcript.resolve()

    session_id, transcript_path = hook_module.extract_session_id_and_transcript_path(
        {"session": {"id": "nested"}, "transcript": {"path": str(transcript)}}
    )
    assert session_id == "nested"
    assert transcript_path == transcript.resolve()


def test_get_session_id_and_transcript_path_fails_open_for_missing_file(hook_module, tmp_path):
    missing_transcript = tmp_path / "missing.jsonl"

    assert hook_module.get_session_id_and_transcript_path(
        {"sessionId": "s1", "transcriptPath": str(missing_transcript)}
    ) is None


def test_session_end_payload_detection_supports_both_key_styles(hook_module):
    assert hook_module.is_session_end_hook_payload({"hook_event_name": "SessionEnd"})
    assert hook_module.is_session_end_hook_payload({"hookEventName": "SessionEnd"})
    assert not hook_module.is_session_end_hook_payload({"hook_event_name": "Stop"})
