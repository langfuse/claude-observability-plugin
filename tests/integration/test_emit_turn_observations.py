from __future__ import annotations


def test_emit_turn_observations_creates_generation_tool_and_subagent_observations(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
    fake_langfuse,
):
    transcript = fixture_transcript_path("async_agent_completed")
    rows = read_fixture_jsonl(transcript)
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    turns = hook_module.build_turns(rows, hook_module.get_task_id_to_tool_use_id(subagents))
    parent_span = fake_langfuse._otel_tracer.start_span(name="parent", start_time=None)

    latest_end_timestamp = hook_module.emit_turn_observations(
        fake_langfuse,
        parent_span,
        turns[0],
        hook_module.parse_timestamp(turns[0].user_msg),
        subagent_transcripts_by_tool_use_id=subagents,
    )

    names = [observation.name for observation in fake_langfuse.observations]
    assert "LLM Call 1" in names
    assert "Tool: Agent" in names
    assert "Subagent: Summarize docs" in names
    assert "Subagent LLM Call 1" in names
    assert "Tool: ToolSearch" in names
    assert latest_end_timestamp.isoformat() == "2026-01-01T00:02:06+00:00"

    agent_tool = next(observation for observation in fake_langfuse.observations if observation.name == "Tool: Agent")
    assert agent_tool.kwargs["metadata"]["subagent_transcript_path"] == "agent-agent-complete.jsonl"
    assert "Async agent launched successfully." in agent_tool.output


def test_nested_subagents_document_current_non_recursive_emission_behavior(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
    fake_langfuse,
):
    transcript = fixture_transcript_path("nested_subagents")
    rows = read_fixture_jsonl(transcript)
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    turns = hook_module.build_turns(rows, hook_module.get_task_id_to_tool_use_id(subagents))
    parent_span = fake_langfuse._otel_tracer.start_span(name="parent", start_time=None)

    hook_module.emit_turn_observations(
        fake_langfuse,
        parent_span,
        turns[0],
        hook_module.parse_timestamp(turns[0].user_msg),
        subagent_transcripts_by_tool_use_id=subagents,
    )

    names = [observation.name for observation in fake_langfuse.observations]
    assert "Subagent: Outer agent" in names
    assert "Tool: Agent" in names
    assert "Subagent: Inner agent" not in names
