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


def test_open_agent_turn_emits_only_after_notification_delivery(
    hook_module,
    fake_langfuse,
    isolated_hook_state,
    tmp_path,
):
    """Exported roots are immutable, so an agent turn must not ship while its
    result is only queued; it ships once, with the real final output, at the
    first firing after delivery + Claude's follow-up response."""
    notification = (
        "<task-notification>\n<task-id>task-1</task-id>\n"
        "<tool-use-id>toolu_agent1</tool-use-id>\n"
        "<status>completed</status>\n<result>42 Ergebnisse</result>\n</task-notification>"
    )
    rows = [
        {"type": "user", "uuid": "user-1", "timestamp": "2026-01-01T00:00:00.000Z",
         "sessionId": "s", "message": {"role": "user", "content": "Starte einen Agent."}},
        {"type": "assistant", "uuid": "a-1", "timestamp": "2026-01-01T00:00:01.000Z",
         "message": {"id": "msg-1", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "toolu_agent1", "name": "Agent",
                                  "input": {"prompt": "recherchiere"}}]}},
        {"type": "user", "uuid": "tr-1", "timestamp": "2026-01-01T00:00:02.000Z",
         "toolUseResult": {"status": "async_launched", "isAsync": True},
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_agent1",
                     "content": [{"type": "text", "text": "Async agent launched successfully."}]}]}},
        {"type": "queue-operation", "operation": "enqueue",
         "timestamp": "2026-01-01T00:00:03.000Z", "content": notification},
        {"type": "assistant", "uuid": "a-2", "timestamp": "2026-01-01T00:00:04.000Z",
         "message": {"id": "msg-2", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "text", "text": "Der Agent laeuft im Hintergrund."}]}},
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    def roots():
        return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]

    # Firing 1: the result is only queued, not delivered — the turn provably
    # continues, so nothing may be exported yet.
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-gate", transcript)
    assert roots() == []

    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "uuid": "n-1", "timestamp": "2026-01-01T00:00:05.000Z",
                            "origin": {"kind": "task-notification"},
                            "message": {"role": "user", "content": notification}}) + "\n")
        f.write(json.dumps({"type": "assistant", "uuid": "a-3", "timestamp": "2026-01-01T00:00:06.000Z",
                            "message": {"id": "msg-3", "role": "assistant", "model": "claude-test",
                                        "content": [{"type": "text", "text": "Fertig: 42 Ergebnisse."}]}}) + "\n")

    # Firing 2: delivered + answered — ships once, with the real final output.
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-gate", transcript)
    assert len(roots()) == 1
    assert roots()[0].output == {"role": "assistant", "content": "Fertig: 42 Ergebnisse."}

    # Firing 3 (idle): the exported root is final; nothing re-opens or duplicates.
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-gate", transcript)
    assert len(roots()) == 1


def test_named_teammate_transcript_attaches_to_its_launch_at_session_end_flush(
    hook_module,
    fake_langfuse,
    isolated_hook_state,
    tmp_path,
):
    """A teammate has no task-notification. The turn still ships at its own
    Stop, and the teammate transcript attaches on a later firing."""
    transcript = tmp_path / "transcript.jsonl"
    launch_rows = [
        {"type": "user", "uuid": "user-1", "timestamp": "2026-01-01T00:00:00.000Z",
         "sessionId": "session-teammate", "message": {"role": "user", "content": "Start a teammate."}},
        {"type": "assistant", "uuid": "assistant-1", "timestamp": "2026-01-01T00:00:01.000Z",
         "message": {"id": "msg-1", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "toolu_builder", "name": "Agent",
                                  "input": {"name": "builder-issue-4",
                                            "taskKind": "in_process_teammate",
                                            "prompt": "Build the fix."}}]}},
        {"type": "user", "uuid": "tool-result-1", "timestamp": "2026-01-01T00:00:02.000Z",
         "toolUseResult": {"status": "teammate_spawned", "name": "builder-issue-4",
                           "agent_id": "builder-issue-4@session-test",
                           "teammate_id": "builder-issue-4@session-test",
                           "agent_type": "general-purpose", "team_name": "session-test"},
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_builder",
              "content": [{"type": "text", "text": "Spawned successfully. agent_id: builder-issue-4@session-test"}]}]}},
        {"type": "assistant", "uuid": "assistant-2", "timestamp": "2026-01-01T00:00:02.500Z",
         "message": {"id": "msg-2", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "text", "text": "The teammate is running."}]}},
    ]
    transcript.write_text("\n".join(json.dumps(row) for row in launch_rows) + "\n", encoding="utf-8")
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    # First Stop: a teammate sends no task-notification. The turn must still
    # ship instead of waiting for one; the teammate transcript is not on disk
    # yet, so its span cannot be emitted in this firing.
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-teammate", transcript)
    names = [observation.name for observation in fake_langfuse.observations]
    assert "Conversational Turn" in names
    assert "Tool: Agent" in names
    assert "Subagent: Build issue 4" not in names

    # The teammate transcript lands after the turn's Stop.
    subagent_dir = tmp_path / "transcript" / "subagents"
    subagent_dir.mkdir(parents=True)
    # Meta and launch shapes below are harvested from a real 2.1.238 team session.
    (subagent_dir / "agent-abuilder-issue-4-03b96faf.meta.json").write_text(
        json.dumps({
            "agentType": "builder-issue-4",
            "description": "Build issue 4",
            "name": "builder-issue-4",
            "spawnDepth": 0,
            "model": "sonnet",
            "taskKind": "in_process_teammate",
            "teamName": "session-test",
            "color": "blue",
            "planModeRequired": False,
            "permissionMode": "default",
        }),
        encoding="utf-8",
    )
    (subagent_dir / "agent-abuilder-issue-4-03b96faf.jsonl").write_text(
        "\n".join(json.dumps(row) for row in [
            {"type": "user", "uuid": "builder-user-1", "timestamp": "2026-01-01T00:00:03.000Z",
             "message": {"role": "user", "content": "Build the fix."}},
            {"type": "assistant", "uuid": "builder-assistant-1", "timestamp": "2026-01-01T00:00:06.000Z",
             "message": {"id": "msg-builder-1", "role": "assistant", "model": "claude-test",
                         "content": [{"type": "text", "text": "Patch ready."}],
                         "usage": {"input_tokens": 12, "output_tokens": 34}}},
        ]) + "\n",
        encoding="utf-8",
    )

    # SessionEnd: the hook attaches the late teammate transcript to the root
    # emitted at Stop, without creating a second root.
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-teammate", transcript, flush_deferred_agent_turns=True,
    )

    names = [observation.name for observation in fake_langfuse.observations]
    assert names.count("Conversational Turn") == 1
    assert "Tool: Agent" in names
    assert "Subagent: Build issue 4" in names
    teammate_span = next(o for o in fake_langfuse.observations if o.name == "Subagent: Build issue 4")
    # Real teammate metas set agentType to the teammate name (config/launch
    # result say "general-purpose"; the meta does not).
    assert teammate_span.kwargs["metadata"]["agent_type"] == "builder-issue-4"
    assert teammate_span.output == {"role": "assistant", "content": "Patch ready."}
    # The trace contains the teammate tokens. 
    teammate_generation = next(o for o in fake_langfuse.observations if o.name == "Subagent LLM Call")
    assert teammate_generation.kwargs["usage_details"] == {"input": 12, "output": 34}


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
