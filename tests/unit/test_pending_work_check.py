from __future__ import annotations


def _write_state(hook_module, session_id, transcript_path, **entry_overrides):
    state = hook_module.load_hook_state()
    key = hook_module.get_session_state_key(session_id, str(transcript_path))
    session_state = hook_module.get_session_state(state, key)
    for field_name, value in entry_overrides.items():
        setattr(session_state, field_name, value)
    hook_module.save_session_state(state, key, session_state)


def test_no_pending_work_when_offset_matches_file_size_and_nothing_deferred(
    hook_module, isolated_hook_state, tmp_path
):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")
    _write_state(hook_module, "s1", transcript, offset=transcript.stat().st_size)

    assert hook_module._has_pending_work("s1", transcript) is False


def test_pending_work_when_transcript_has_unread_bytes(hook_module, isolated_hook_state, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")
    _write_state(hook_module, "s1", transcript, offset=0)

    assert hook_module._has_pending_work("s1", transcript) is True


def test_pending_work_when_agent_turns_are_deferred(hook_module, isolated_hook_state, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")
    _write_state(
        hook_module,
        "s1",
        transcript,
        offset=transcript.stat().st_size,
        pending_agent_turns=[{"turn": 1}],
    )

    assert hook_module._has_pending_work("s1", transcript) is True


def test_pending_work_when_task_notifications_are_unresolved(hook_module, isolated_hook_state, tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")
    _write_state(
        hook_module,
        "s1",
        transcript,
        offset=transcript.stat().st_size,
        pending_task_notifications=[{"tool_use_id": "abc"}],
    )

    assert hook_module._has_pending_work("s1", transcript) is True


def test_pending_work_defaults_true_for_unknown_session(hook_module, isolated_hook_state, tmp_path):
    # No saved state for this session yet (e.g. state file wiped, or a hook race) -
    # fail open and take the real path rather than risk silently dropping data.
    transcript = tmp_path / "session.jsonl"
    transcript.write_text("", encoding="utf-8")

    assert hook_module._has_pending_work("never-seen", transcript) is True


def test_main_skips_langfuse_client_creation_when_nothing_pending(
    hook_module, isolated_hook_state, tmp_path, monkeypatch
):
    import io
    import json
    import sys

    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")
    _write_state(hook_module, "s1", transcript, offset=transcript.stat().st_size)

    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"sessionId": "s1", "transcriptPath": str(transcript)}))
    )
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")

    called = False

    def _boom(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("create_langfuse_client should not run when nothing is pending")

    monkeypatch.setattr(hook_module, "create_langfuse_client", _boom)

    assert hook_module.main() == 0
    assert called is False
