from __future__ import annotations

import json


def test_emit_new_turns_from_completed_async_agent_transcript(
    hook_module,
    fixture_transcript_path,
    fake_langfuse,
    isolated_hook_state,
):
    transcript = fixture_transcript_path("async_agent_completed")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    # First Stop: the trailing turn could still be continued (Stop fires
    # multiple times within one logical turn), so it is held open.
    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse,
        config,
        "session-agent-complete",
        transcript,
    )

    assert emitted == 0
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    assert next(iter(state.values()))["open_turn"]["rows"]

    # SessionEnd closes it.
    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse,
        config,
        "session-agent-complete",
        transcript,
        flush_deferred_agent_turns=True,
    )

    assert emitted == 1
    assert any(observation.name == "Conversational Turn" for observation in fake_langfuse.observations)
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    assert next(iter(state.values()))["turn_count"] == 1
    assert next(iter(state.values()))["pending_agent_turns"] == []
    assert next(iter(state.values()))["open_turn"] == {}


def test_emit_new_turns_defers_async_agent_until_session_end_flush(
    hook_module,
    fixture_transcript_path,
    fake_langfuse,
    isolated_hook_state,
):
    transcript = fixture_transcript_path("async_agent_deferred")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse,
        config,
        "session-agent-deferred",
        transcript,
    )

    # The trailing turn is held open (its async agent may still notify and
    # Claude may continue), so it is neither emitted nor deferred yet.
    assert emitted == 0
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    assert next(iter(state.values()))["pending_agent_turns"] == []
    assert next(iter(state.values()))["open_turn"]["rows"]

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse,
        config,
        "session-agent-deferred",
        transcript,
        flush_deferred_agent_turns=True,
    )

    assert emitted == 1
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    assert next(iter(state.values()))["turn_count"] == 1
    assert next(iter(state.values()))["pending_agent_turns"] == []
