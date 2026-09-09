from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SESSION = "cccccccc-3333-3333-3333-333333333333"


def user_row(session_id: str, index: int, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-08-11T14:{index:02d}:00.000Z",
        "sessionId": session_id,
        "uuid": f"user-row-{index}",
        "message": {"role": "user", "content": text},
    }


def assistant_row(
    session_id: str, index: int, content: Any, *, part: int = 0
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": f"2026-08-11T14:{index:02d}:{10 + part}.000Z",
        "sessionId": session_id,
        "uuid": f"assistant-row-{index}-{part}",
        "message": {
            "id": f"msg-{index}-{part}",
            "role": "assistant",
            "model": "claude-test",
            "content": content,
            "usage": {"input_tokens": 2, "output_tokens": 540},
        },
    }


def user_row_with_image(session_id: str, index: int, text: str) -> dict[str, Any]:
    """A user row with a pasted image, like Claude Code writes it."""
    return {
        "type": "user",
        "timestamp": f"2026-08-11T14:{index:02d}:00.000Z",
        "sessionId": session_id,
        "uuid": f"user-row-{index}",
        "message": {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/png", "data": "aGVsbG8="}},
        ]},
    }


def tool_result_row(session_id: str, index: int, tool_use_id: str, text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-08-11T14:{index:02d}:20.000Z",
        "sessionId": session_id,
        "uuid": f"tool-result-{tool_use_id}",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": tool_use_id, "content": text}
            ],
        },
    }


def simple_turn_rows(session_id: str, index: int) -> list[dict[str, Any]]:
    return [
        user_row(session_id, index, f"Question {index}?"),
        assistant_row(session_id, index, [{"type": "text", "text": f"Answer {index}."}]),
    ]


def tool_turn_rows(session_id: str, index: int) -> list[dict[str, Any]]:
    """A turn with two generations: tool call first, then the answer."""
    tool_use_id = f"toolu-{index}"
    return [
        user_row(session_id, index, f"Question {index}?"),
        assistant_row(
            session_id, index,
            [{"type": "tool_use", "id": tool_use_id, "name": "Read",
              "input": {"file_path": "README.md"}}],
            part=0,
        ),
        tool_result_row(session_id, index, tool_use_id, "file content"),
        assistant_row(
            session_id, index,
            [{"type": "text", "text": f"Answer {index}."}],
            part=1,
        ),
    ]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def config_for(hook_module: Any) -> Any:
    return hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")


def generations(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.as_type == "generation"]


def generation_inputs(fake_langfuse: Any) -> list[Any]:
    return [g.kwargs.get("input") for g in generations(fake_langfuse)]


def test_generation_input_contains_prior_turns(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows: list[dict[str, Any]] = []
    for index in (1, 2, 3):
        rows.extend(simple_turn_rows(SESSION, index))
    write_rows(transcript, rows)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    inputs = generation_inputs(fake_langfuse)
    assert len(inputs) == 3
    # Turn 1 has no history: only its own user message.
    assert inputs[0] == [{"role": "user", "content": "Question 1?"}]
    # Turn 3 sees turns 1 and 2 in order, then its own user message.
    third = inputs[2]
    assert [m.get("role") for m in third] == ["user", "assistant", "user", "assistant", "user"]
    assert third[0]["content"] == "Question 1?"
    assert third[1]["content"] == "Answer 1."
    assert third[-1] == {"role": "user", "content": "Question 3?"}


def test_generation_input_within_turn_contains_tool_steps(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, tool_turn_rows(SESSION, 1) + simple_turn_rows(SESSION, 2))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    inputs = generation_inputs(fake_langfuse)
    assert len(inputs) == 3
    # Second generation of turn 1: user, tool-call assistant, tool results.
    second = inputs[1]
    assert [m.get("role") for m in second] == ["user", "assistant", "tool"]
    assert second[1]["tool_calls"] == [
        {"id": "toolu-1", "type": "function", "function": {"name": "Read"}}
    ]
    assert second[2]["tool_results"][0]["tool_use_id"] == "toolu-1"
    # Turn 2 sees the full tool exchange of turn 1.
    third = inputs[2]
    assert [m.get("role") for m in third] == ["user", "assistant", "tool", "assistant", "user"]


def test_history_survives_separate_firings(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows = simple_turn_rows(SESSION, 1) + simple_turn_rows(SESSION, 2)
    write_rows(transcript, rows)
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)

    # The next firing reads only new bytes for emission, but the history
    # comes from the whole file.
    append_rows(transcript, simple_turn_rows(SESSION, 3))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)

    third = generation_inputs(fake_langfuse)[-1]
    assert third[0] == {"role": "user", "content": "Question 1?"}
    assert third[-1] == {"role": "user", "content": "Question 3?"}
    assert len(third) == 5


def test_root_span_input_stays_this_turns_question(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # The trace list must stay scannable, so the root shows the new question
    # only. The conversation history belongs on the LLM call inputs.
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows: list[dict[str, Any]] = []
    for index in (1, 2, 3):
        rows.extend(simple_turn_rows(SESSION, index))
    write_rows(transcript, rows)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    roots = [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]
    assert [r.kwargs["input"] for r in roots] == [
        {"role": "user", "content": "Question 1?"},
        {"role": "user", "content": "Question 2?"},
        {"role": "user", "content": "Question 3?"},
    ]
    for root in roots:
        assert "history" not in root.kwargs["metadata"]
    # The third turn's generation does carry the history.
    assert len(generation_inputs(fake_langfuse)[2]) == 5


def test_history_keeps_images_at_their_turn(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows: list[dict[str, Any]] = [
        user_row_with_image(SESSION, 1, "Look at this."),
        assistant_row(SESSION, 1, [{"type": "text", "text": "Answer 1."}]),
    ]
    rows.extend(simple_turn_rows(SESSION, 2))
    write_rows(transcript, rows)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    # Turn 2's input repeats turn 1 - including its image, at turn 1's position.
    second = generation_inputs(fake_langfuse)[1]
    first_message_content = second[0]["content"]
    assert isinstance(first_message_content, list)
    # The text keeps its image marker, the media object follows it.
    assert first_message_content[0].startswith("Look at this.")
    assert "[image image/png" in first_message_content[0]
    assert type(first_message_content[1]).__name__ == "LangfuseMedia"
    # The root span of turn 1 shows the image too, but without the history.
    # Its media object is a separate instance with the same content; Langfuse
    # deduplicates uploads by content hash.
    roots = [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]
    root_content = roots[0].kwargs["input"]["content"]
    assert root_content[0] == first_message_content[0]
    assert type(root_content[1]).__name__ == "LangfuseMedia"
    assert root_content[1].base64_data_uri == first_message_content[1].base64_data_uri
    assert roots[1].kwargs["input"] == {"role": "user", "content": "Question 2?"}


def test_history_has_no_length_limit(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # Long tool outputs used to be cut by a byte budget; nothing is dropped now.
    transcript = tmp_path / f"{SESSION}.jsonl"
    rows: list[dict[str, Any]] = []
    for index in (1, 2, 3, 4):
        tool_use_id = f"toolu-{index}"
        rows.extend([
            user_row(SESSION, index, f"Question {index}?"),
            assistant_row(SESSION, index, [{"type": "tool_use", "id": tool_use_id,
                                            "name": "Read", "input": {}}], part=0),
            tool_result_row(SESSION, index, tool_use_id, "x" * 30000),
            assistant_row(SESSION, index, [{"type": "text", "text": f"Answer {index}."}], part=1),
        ])
    write_rows(transcript, rows)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    last_input = generation_inputs(fake_langfuse)[-1]
    # 4 turns x 4 messages, minus the last turn's unfinished steps.
    assert len(last_input) >= 13
    assert last_input[0] == {"role": "user", "content": "Question 1?"}
    # Each tool result obeys CC_LANGFUSE_MAX_CHARS on its own, but the
    # history keeps every one of them, so it grows past that budget.
    tool_output_chars = sum(
        len(result["output"])
        for message in last_input
        if message.get("role") == "tool"
        for result in message["tool_results"]
    )
    assert tool_output_chars > hook_module.MAX_CHARS
    # The reported message count is the real one, so nothing was dropped.
    for generation in generations(fake_langfuse):
        assert generation.kwargs["metadata"]["history"]["messages"] == len(
            generation.kwargs["input"]
        )


def test_delta_inputs_when_the_history_cannot_be_rebuilt(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch
):
    # A transcript the history pass cannot read leaves no messages to attach.
    # Emission then continues with the delta inputs.
    monkeypatch.setattr(hook_module, "build_session_history", lambda *a, **kw: None)
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, simple_turn_rows(SESSION, 1) + simple_turn_rows(SESSION, 2))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    inputs = generation_inputs(fake_langfuse)
    assert inputs[0] == {"role": "user", "content": "Question 1?"}
    assert inputs[1] == {"role": "user", "content": "Question 2?"}
    for generation in generations(fake_langfuse):
        assert "history" not in generation.kwargs["metadata"]


def test_subagent_generations_get_history_of_their_own_transcript(
    hook_module, fake_langfuse, tmp_path
):
    subagent_path = tmp_path / "agent.jsonl"
    write_rows(
        subagent_path,
        simple_turn_rows("agent-session", 1) + simple_turn_rows("agent-session", 2),
    )

    hook_module.emit_subagent_observations(
        fake_langfuse,
        None,
        {"path": subagent_path, "agent_type": "test", "description": "demo"},
        None,
    )

    inputs = generation_inputs(fake_langfuse)
    assert len(inputs) == 2
    assert inputs[0] == [{"role": "user", "content": "Question 1?"}]
    assert inputs[1][0] == {"role": "user", "content": "Question 1?"}
    assert inputs[1][-1] == {"role": "user", "content": "Question 2?"}


def test_the_turn_with_the_image_sees_it_in_its_own_input(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # The turn that received the image must show it in its own generation
    # input, in the same shape later turns see it in the history.
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, [
        user_row_with_image(SESSION, 1, "Look at this."),
        assistant_row(SESSION, 1, [{"type": "text", "text": "Answer 1."}]),
    ])

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    own_input = generation_inputs(fake_langfuse)[0]
    content = own_input[0]["content"]
    assert isinstance(content, list)
    assert content[0].startswith("Look at this.")
    assert type(content[1]).__name__ == "LangfuseMedia"


def test_generation_emitted_in_a_later_firing_carries_the_full_history(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # A turn can span several Stop firings. The generation that ships in the
    # second firing must see the steps that shipped in the first one.
    transcript = tmp_path / f"{SESSION}.jsonl"
    tool_use_id = "toolu-1"
    write_rows(transcript, [
        user_row(SESSION, 1, "Question 1?"),
        assistant_row(SESSION, 1, [{"type": "tool_use", "id": tool_use_id, "name": "Read",
                                    "input": {"file_path": "README.md"}}], part=0),
        tool_result_row(SESSION, 1, tool_use_id, "file content"),
    ])
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)
    first_firing = [g.kwargs["input"] for g in generations(fake_langfuse)]
    assert first_firing == [[{"role": "user", "content": "Question 1?"}]]

    append_rows(transcript, [
        assistant_row(SESSION, 1, [{"type": "text", "text": "Answer 1."}], part=1),
    ] + simple_turn_rows(SESSION, 2))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, transcript)

    inputs = generation_inputs(fake_langfuse)
    # The first generation is not emitted again; the second one sees the
    # tool exchange; turn 2 sees the whole of turn 1.
    assert len(inputs) == 3
    assert [m["role"] for m in inputs[1]] == ["user", "assistant", "tool"]
    assert [m["role"] for m in inputs[2]] == ["user", "assistant", "tool", "assistant", "user"]


def test_deferred_agent_turn_flushed_at_session_end_gets_history(
    hook_module, fake_langfuse, isolated_hook_state, fixture_transcript_path
):
    transcript = fixture_transcript_path("async_agent_deferred")
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-deferred", transcript)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-deferred", transcript, flush_deferred_agent_turns=True
    )

    inputs = generation_inputs(fake_langfuse)
    assert inputs
    for generation_input in inputs:
        assert isinstance(generation_input, list)
        assert generation_input[0]["role"] == "user"


def test_workflow_agent_generations_carry_history(
    hook_module, fake_langfuse, isolated_hook_state, fixture_transcript_path
):
    transcript = fixture_transcript_path("workflow_subagents")
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-workflow", transcript)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-workflow", transcript, flush_deferred_agent_turns=True
    )

    inputs = generation_inputs(fake_langfuse)
    assert inputs
    for generation_input in inputs:
        assert isinstance(generation_input, list)
    for generation in generations(fake_langfuse):
        assert "history" in generation.kwargs["metadata"]


# ---- tool_calls shape: one producer, every path ----

def test_history_tool_calls_come_from_the_one_shape_builder(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    """Pins the history path to build_generation_output."""
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, tool_turn_rows(SESSION, 1) + simple_turn_rows(SESSION, 2))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    expected = hook_module.build_generation_output("", [{"id": "toolu-1", "name": "Read"}])
    live_generation = generation_inputs(fake_langfuse)[1][1]
    later_turn_history = generation_inputs(fake_langfuse)[2][1]

    assert live_generation["tool_calls"] == expected["tool_calls"]
    assert later_turn_history["tool_calls"] == expected["tool_calls"]


def test_every_tool_call_the_hook_emits_has_the_nested_keys(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, tool_turn_rows(SESSION, 1) + tool_turn_rows(SESSION, 2))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    seen = 0
    for messages in generation_inputs(fake_langfuse):
        for message in messages or []:
            for tool_call in (message or {}).get("tool_calls", []):
                assert set(tool_call) == {"id", "type", "function"}
                assert tool_call["type"] == "function"
                assert set(tool_call["function"]) == {"name"}
                seen += 1
    assert seen > 0, "no tool_calls reached the history, the assertion proved nothing"


def test_a_tool_use_without_a_name_keeps_the_nested_shape_in_the_history(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{SESSION}.jsonl"
    write_rows(transcript, [
        user_row(SESSION, 1, "Question 1?"),
        assistant_row(SESSION, 1, [{"type": "tool_use", "id": "toolu-x"}], part=0),
        tool_result_row(SESSION, 1, "toolu-x", "result"),
        assistant_row(SESSION, 1, [{"type": "text", "text": "Answer 1."}], part=1),
        *simple_turn_rows(SESSION, 2),
    ])

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), SESSION, transcript
    )

    assert generation_inputs(fake_langfuse)[1][1]["tool_calls"] == [
        {"id": "toolu-x", "type": "function", "function": {"name": None}}
    ]
