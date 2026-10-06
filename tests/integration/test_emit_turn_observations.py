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
    assert "LLM Call" in names
    assert "Tool: Agent" in names
    assert "Subagent: Summarize docs" in names
    assert "Subagent LLM Call" in names
    assert "Tool: ToolSearch" in names
    assert latest_end_timestamp.isoformat() == "2026-01-01T00:02:06+00:00"

    agent_tool = next(observation for observation in fake_langfuse.observations if observation.name == "Tool: Agent")
    assert agent_tool.kwargs["metadata"]["subagent_transcript_path"] == "agent-agent-complete.jsonl"
    assert "Async agent launched successfully." in agent_tool.output


def test_nested_subagents_emit_inner_agent_under_outer_agent(
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

    by_name = {observation.name: observation for observation in fake_langfuse.observations}
    outer = by_name["Subagent: Outer agent"]
    inner = by_name["Subagent: Inner agent"]

    ancestors = []
    span = inner._otel_span.parent
    while span is not None:
        ancestors.append(span)
        span = span.parent
    assert outer._otel_span in ancestors
    assert any(
        observation._otel_span.parent is inner._otel_span and observation.as_type == "generation"
        for observation in fake_langfuse.observations
    )


# The fixture's human wait: the AskUserQuestion tool_use is written at
# 00:01:08 and its result only at 03:01:08, when the human answered.
WAIT_TURN_START = "2026-01-01T00:01:00.000Z"
WAIT_QUESTION_ASKED = "2026-01-01T00:01:08.000Z"
WAIT_ANSWER_ARRIVED = "2026-01-01T03:01:08.000Z"
WAIT_TURN_END = "2026-01-01T03:01:14.000Z"


def test_a_human_wait_on_a_tool_stays_off_the_generation_that_called_it(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
    fake_langfuse,
):
    """A generation must not absorb the wait of a tool it launched.

    The tool result timestamp is when the tool finished, which for
    AskUserQuestion is when the human answered. Ending the generation there
    reported three hours of model latency for 1742 output tokens.
    """
    def to_ns(iso_timestamp: str) -> int:
        return hook_module.to_otel_nanoseconds(hook_module.parse_timestamp(iso_timestamp))

    transcript = fixture_transcript_path("human_wait_question")
    rows = read_fixture_jsonl(transcript)
    turns = hook_module.build_turns(rows, {})

    latest_end_timestamp = hook_module.emit_turn_observations(
        fake_langfuse,
        fake_langfuse._otel_tracer.start_span(name="parent", start_time=None),
        turns[0],
        hook_module.parse_timestamp(turns[0].user_msg),
    )

    generations = [o for o in fake_langfuse.observations if o.as_type == "generation"]
    asking_generation, answering_generation = generations
    question_tool = next(
        o for o in fake_langfuse.observations if o.name == "Tool: AskUserQuestion"
    )

    # The generation that asked ends at its own assistant row, so its
    # duration stays the eight seconds the model took.
    assert asking_generation.kwargs["usage_details"]["output"] == 1742
    assert asking_generation._otel_span.start_time == to_ns(WAIT_TURN_START)
    assert asking_generation.end_time == to_ns(WAIT_QUESTION_ASKED)

    # The three-hour wait belongs to the tool alone.
    assert question_tool._otel_span.start_time == to_ns(WAIT_QUESTION_ASKED)
    assert question_tool.end_time == to_ns(WAIT_ANSWER_ARRIVED)

    # No generation overlaps the wait interval.
    assert all(
        generation.end_time <= to_ns(WAIT_QUESTION_ASKED)
        or generation._otel_span.start_time >= to_ns(WAIT_ANSWER_ARRIVED)
        for generation in generations
    )

    # The answer is the input of the next generation, which starts when the
    # answer arrived.
    assert answering_generation._otel_span.start_time == to_ns(WAIT_ANSWER_ARRIVED)
    assert answering_generation.end_time == to_ns(WAIT_TURN_END)

    # The turn span still covers the whole turn, wait included.
    assert latest_end_timestamp == hook_module.parse_timestamp(WAIT_TURN_END)
