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


def test_idle_firings_send_no_root_updates(
    hook_module,
    fixture_transcript_path,
    fake_langfuse,
    isolated_hook_state,
    recorded_ingestion_events,
):
    # Every queued update becomes a row version server-side; idle firings
    # (nothing newly emitted) must therefore stay silent, close must not.
    transcript = fixture_transcript_path("async_agent_deferred")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-gate", transcript)
    after_first_firing = len(recorded_ingestion_events)

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-gate", transcript)
    assert len(recorded_ingestion_events) == after_first_firing

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-gate", transcript, flush_deferred_agent_turns=True
    )
    update_types = [e["type"] for e in recorded_ingestion_events[after_first_firing:]]
    assert "span-update" in update_types


def test_only_the_turn_root_span_is_marked_as_root(
    hook_module,
    fixture_transcript_path,
    fake_langfuse,
    isolated_hook_state,
):
    # The root span sits under a synthetic trace-id carrier, so its exported
    # parentSpanId never resolves; without the as_root marker the server
    # creates only a shallow trace and drops trace-level input/output.
    transcript = fixture_transcript_path("tool_turn")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-as-root", transcript)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-as-root", transcript, flush_deferred_agent_turns=True
    )

    roots = [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]
    children = [o for o in fake_langfuse.observations if o.name != "Conversational Turn"]
    assert roots and children
    assert all(o._otel_span.attributes.get("langfuse.internal.as_root") is True for o in roots)
    assert all("langfuse.internal.as_root" not in o._otel_span.attributes for o in children)
    # Children explicitly opt out of the SDK's app-root auto-marking, so the
    # events view never lists them as root observations.
    assert all(o._otel_span.attributes.get("langfuse.internal.is_app_root") is False for o in children)
