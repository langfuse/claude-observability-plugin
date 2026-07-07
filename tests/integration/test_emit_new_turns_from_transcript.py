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

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse,
        config,
        "session-agent-complete",
        transcript,
    )

    assert emitted == 1
    assert any(observation.name == "Conversational Turn" for observation in fake_langfuse.observations)
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    assert next(iter(state.values()))["turn_count"] == 1
    assert next(iter(state.values()))["pending_agent_turns"] == []


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

    assert emitted == 0
    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    pending_agent_turns = next(iter(state.values()))["pending_agent_turns"]
    assert len(pending_agent_turns) == 1
    assert pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_deferred"]

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
