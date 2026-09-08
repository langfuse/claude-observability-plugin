from __future__ import annotations

import json

import pytest


@pytest.fixture
def level_turns(hook_module, fixture_transcript_path, read_fixture_jsonl):
    transcript = fixture_transcript_path("observation_levels")
    rows = read_fixture_jsonl(transcript)
    return hook_module.build_turns(rows, {})


def emit(hook_module, fake_langfuse, turn):
    parent_span = fake_langfuse._otel_tracer.start_span(name="parent", start_time=None)
    hook_module.emit_turn_observations(
        fake_langfuse,
        parent_span,
        turn,
        hook_module.parse_timestamp(turn.user_msg),
    )
    return fake_langfuse.observations


def tool_by_id(observations, tool_id):
    return next(
        observation
        for observation in observations
        if observation.as_type == "tool" and observation.kwargs["metadata"]["tool_id"] == tool_id
    )


def test_a_failed_tool_result_marks_its_tool_observation_as_error(
    hook_module, fake_langfuse, level_turns
):
    observations = emit(hook_module, fake_langfuse, level_turns[0])

    failed = tool_by_id(observations, "toolu_fail")
    assert failed.kwargs["level"] == "ERROR"
    assert failed.kwargs["status_message"] == "cat: /nope: No such file or directory"


@pytest.mark.parametrize("tool_id", ["toolu_false", "toolu_absent"])
def test_a_tool_result_without_an_error_flag_keeps_the_default_level(
    hook_module, fake_langfuse, level_turns, tool_id
):
    observations = emit(hook_module, fake_langfuse, level_turns[0])

    succeeded = tool_by_id(observations, tool_id)
    assert "level" not in succeeded.kwargs
    assert "status_message" not in succeeded.kwargs


def test_an_interrupted_turn_marks_its_root_span_as_warning(
    hook_module, fake_langfuse, level_turns, tmp_path
):
    interrupted = level_turns[1]

    root = hook_module.open_turn_root_span(
        fake_langfuse, "session-levels", 2, interrupted, tmp_path / "t.jsonl"
    )

    assert root.kwargs["level"] == "WARNING"
    assert root.kwargs["status_message"] == "Turn interrupted by user"


def test_the_interrupt_marker_stays_in_the_turn_it_ended(level_turns):
    # A turn of its own would carry no assistant row and be dropped, losing
    # both the marker and the level it implies.
    assert len(level_turns) == 4
    interrupted = level_turns[1]
    assert interrupted.user_msg["uuid"] == "u2"
    assert any(row["uuid"] == "int1" for row in interrupted.rows)


def test_an_interrupt_bounds_the_turn_it_ended(hook_module, level_turns):
    end_timestamp = hook_module.get_turn_end_timestamp(level_turns[1])

    assert end_timestamp.isoformat() == "2026-01-01T00:02:02+00:00"


def test_an_api_error_that_ends_the_turn_marks_the_turn_as_error(
    hook_module, fake_langfuse, level_turns, tmp_path
):
    root = hook_module.open_turn_root_span(
        fake_langfuse, "session-levels", 3, level_turns[2], tmp_path / "t.jsonl"
    )

    assert root.kwargs["level"] == "ERROR"
    assert root.kwargs["status_message"] == "API Error: 500 Internal Server Error"


def test_a_failed_api_call_marks_its_own_generation_as_error(
    hook_module, fake_langfuse, level_turns
):
    observations = emit(hook_module, fake_langfuse, level_turns[2])

    generation = next(
        observation for observation in observations if observation.as_type == "generation"
    )
    assert generation.kwargs["level"] == "ERROR"
    assert generation.kwargs["status_message"] == "API Error: 500 Internal Server Error"


def test_an_api_error_the_turn_recovered_from_leaves_the_turn_at_the_default_level(
    hook_module, fake_langfuse, level_turns, tmp_path
):
    recovered = level_turns[3]

    root = hook_module.open_turn_root_span(
        fake_langfuse, "session-levels", 4, recovered, tmp_path / "t.jsonl"
    )
    generations = [
        observation
        for observation in emit(hook_module, fake_langfuse, recovered)
        if observation.as_type == "generation"
    ]

    assert "level" not in root.kwargs
    assert [generation.kwargs.get("level") for generation in generations] == ["ERROR", None]


def test_an_error_that_ends_the_turn_outranks_an_interrupt(hook_module, level_turns):
    interrupted = level_turns[1]
    interrupted.assistant_msgs.append(
        {"isApiErrorMessage": True, "message": {"role": "assistant", "content": "API Error: boom"}}
    )

    assert hook_module.get_turn_status(interrupted) == ("ERROR", "API Error: boom")


def test_a_status_message_keeps_the_whole_error_text(hook_module):
    message = hook_module.build_status_message("x" * 5000, "fallback")

    assert message == "x" * 5000


def test_a_status_message_falls_back_when_there_is_no_text(hook_module):
    assert hook_module.build_status_message("", "fallback") == "fallback"
    assert hook_module.build_status_message(None, "fallback") == "fallback"


def subagent_prompt(uuid, text, timestamp):
    return {
        "type": "user",
        "agentId": "a0",
        "sessionId": "session-sub",
        "uuid": uuid,
        "timestamp": timestamp,
        "message": {"role": "user", "content": text},
    }


def subagent_answer(uuid, text, timestamp, api_error=False):
    row = {
        "type": "assistant",
        "agentId": "a0",
        "sessionId": "session-sub",
        "uuid": uuid,
        "requestId": f"req-{uuid}",
        "timestamp": timestamp,
        "message": {
            "id": f"msg-{uuid}",
            "role": "assistant",
            "model": "<synthetic>" if api_error else "claude-test",
            "content": [{"type": "text", "text": text}],
        },
    }
    if api_error:
        row["isApiErrorMessage"] = True
    return row


def subagent_interrupt(uuid, timestamp):
    return {
        "type": "user",
        "agentId": "a0",
        "sessionId": "session-sub",
        "uuid": uuid,
        "timestamp": timestamp,
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": "[Request interrupted by user]"}],
        },
    }


def emit_subagent(hook_module, fake_langfuse, tmp_path, rows):
    path = tmp_path / "agent-sub.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    parent_span = fake_langfuse._otel_tracer.start_span(name="parent", start_time=None)
    hook_module.emit_subagent_observations(
        fake_langfuse,
        parent_span,
        {"path": path, "description": "Check things", "agent_type": "general-purpose"},
        None,
    )
    return next(
        observation
        for observation in fake_langfuse.observations
        if observation.name == "Subagent: Check things"
    )


def test_an_interrupted_subagent_marks_its_own_span_as_warning(
    hook_module, fake_langfuse, tmp_path
):
    # The subagent answered before it was stopped, so the turn exists and the
    # interrupt has somewhere to land.
    span = emit_subagent(
        hook_module,
        fake_langfuse,
        tmp_path,
        [
            subagent_prompt("s1", "Check the repo.", "2026-01-01T00:00:00.000Z"),
            subagent_answer("s2", "Looking now.", "2026-01-01T00:00:01.000Z"),
            subagent_interrupt("s3", "2026-01-01T00:00:02.000Z"),
        ],
    )

    assert span.kwargs["level"] == "WARNING"
    assert span.kwargs["status_message"] == "Turn interrupted by user"


def test_a_failed_subagent_marks_its_own_span_as_error(hook_module, fake_langfuse, tmp_path):
    span = emit_subagent(
        hook_module,
        fake_langfuse,
        tmp_path,
        [
            subagent_prompt("s1", "Check the repo.", "2026-01-01T00:00:00.000Z"),
            subagent_answer("s2", "API Error: 500", "2026-01-01T00:00:01.000Z", api_error=True),
        ],
    )

    assert span.kwargs["level"] == "ERROR"
    assert span.kwargs["status_message"] == "API Error: 500"


def test_a_subagent_span_reports_its_worst_turn(hook_module, fake_langfuse, tmp_path):
    span = emit_subagent(
        hook_module,
        fake_langfuse,
        tmp_path,
        [
            subagent_prompt("s1", "First job.", "2026-01-01T00:00:00.000Z"),
            subagent_answer("s2", "Working.", "2026-01-01T00:00:01.000Z"),
            subagent_interrupt("s3", "2026-01-01T00:00:02.000Z"),
            subagent_prompt("s4", "Second job.", "2026-01-01T00:00:03.000Z"),
            subagent_answer("s5", "API Error: 529", "2026-01-01T00:00:04.000Z", api_error=True),
        ],
    )

    assert span.kwargs["level"] == "ERROR"


def test_a_healthy_subagent_span_keeps_the_default_level(hook_module, fake_langfuse, tmp_path):
    span = emit_subagent(
        hook_module,
        fake_langfuse,
        tmp_path,
        [
            subagent_prompt("s1", "Check the repo.", "2026-01-01T00:00:00.000Z"),
            subagent_answer("s2", "All good.", "2026-01-01T00:00:01.000Z"),
        ],
    )

    assert "level" not in span.kwargs


def test_a_denied_tool_does_not_interrupt_the_whole_turn(hook_module):
    denial = {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "[Request interrupted by user for tool use]"}
            ],
        },
    }

    assert hook_module.is_interrupted_turn_row(denial) is False
