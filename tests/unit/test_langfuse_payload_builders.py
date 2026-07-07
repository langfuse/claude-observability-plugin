from __future__ import annotations


def test_generation_input_tracks_user_tool_and_async_tool_contexts(hook_module):
    assert hook_module.build_generation_input(0, "hello", [], []) == {
        "role": "user",
        "content": "hello",
    }
    assert hook_module.build_generation_input(
        1,
        "ignored",
        [{"tool_use_id": "toolu_read", "output": "file"}],
        [],
    ) == {"role": "tool", "tool_results": [{"tool_use_id": "toolu_read", "output": "file"}]}
    assert hook_module.build_generation_input(
        1,
        "ignored",
        [],
        [{"tool_result": {"tool_use_id": "toolu_agent", "output": "done"}}],
    ) == {"role": "tool", "tool_results": [{"tool_use_id": "toolu_agent", "output": "done"}]}
    assert hook_module.build_generation_input(1, "ignored", [], []) is None


def test_generation_input_merges_previous_batch_and_ready_async_results(hook_module):
    # Regression: a previous tool batch used to shadow async results that
    # became ready at the same time; they are popped from the pending list
    # when consumed, so dropping them here lost them for good.
    assert hook_module.build_generation_input(
        1,
        "ignored",
        [{"tool_use_id": "toolu_read", "output": "file"}],
        [{"tool_result": {"tool_use_id": "toolu_agent", "output": "done"}}],
    ) == {
        "role": "tool",
        "tool_results": [
            {"tool_use_id": "toolu_read", "output": "file"},
            {"tool_use_id": "toolu_agent", "output": "done"},
        ],
    }


def test_generation_output_lists_tool_calls_without_full_tool_input(hook_module):
    output = hook_module.build_generation_output(
        "I will read it.",
        [{"id": "toolu_read", "name": "Read", "input": {"file_path": "README.md"}}],
    )

    assert output == {
        "role": "assistant",
        "content": "I will read it.",
        "tool_calls": [{"id": "toolu_read", "name": "Read"}],
    }


def test_tool_result_payload_preserves_initial_and_final_async_outputs(hook_module):
    result = hook_module.get_tool_result_for_observation(
        {
            "content": "Async agent launched successfully.",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "final_content": "Final async output",
            "final_timestamp": "2026-01-01T00:00:05.000Z",
        }
    )

    assert result.output == "Async agent launched successfully."
    assert result.final_output == "Final async output"
    assert result.result_timestamp.isoformat() == "2026-01-01T00:00:01+00:00"
    assert result.final_result_timestamp.isoformat() == "2026-01-01T00:00:05+00:00"


def test_tool_metadata_uses_short_subagent_transcript_paths(hook_module, tmp_path):
    metadata = hook_module.build_tool_metadata(
        "Agent",
        "toolu_agent",
        None,
        hook_module.ToolResultForObservation(output_meta={"truncated": False}),
        {
            "agent_type": "general-purpose",
            "description": "Summarize docs",
            "path": tmp_path / "agent-a123.jsonl",
        },
    )

    assert metadata["tool_name"] == "Agent"
    assert metadata["tool_id"] == "toolu_agent"
    assert metadata["subagent_type"] == "general-purpose"
    assert metadata["subagent_description"] == "Summarize docs"
    assert metadata["subagent_transcript_path"] == "agent-a123.jsonl"


def test_trace_metadata_includes_session_turn_project_and_branch(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    rows = read_fixture_jsonl(fixture_transcript_path("simple_turn"))
    turn = hook_module.build_turns(rows)[0]
    user_text, user_text_meta = hook_module.truncate_text(
        hook_module.extract_text_from_content(hook_module.get_content_from_row(turn.user_msg))
    )

    metadata = hook_module.build_trace_metadata(
        "12345678-abcd-4000-8000-123456789abc",
        7,
        turn,
        fixture_transcript_path("simple_turn"),
        user_text_meta,
    )

    assert user_text == "Say hello."
    assert metadata["session_id"] == "12345678-abcd-4000-8000-123456789abc"
    assert metadata["turn_number"] == 7
    assert metadata["transcript_path"] == "transcript.jsonl"
    assert metadata["cwd"] == "/repo"
    assert metadata["git_branch"] == "feature/test"


def test_async_final_result_reaches_generation_input_despite_tool_batch(
    hook_module,
    fake_langfuse,
):
    """Regression: when a normal tool result and a ready async agent result
    both precede an assistant message, the async final result vanished from
    every generation input."""
    rows = [
        {"type": "user", "timestamp": "2026-01-01T00:00:00.000Z", "uuid": "u1",
         "message": {"role": "user", "content": "Start agent then run a command."}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:01.000Z", "uuid": "a1",
         "message": {"id": "msg-1", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "toolu_bg", "name": "Agent", "input": {}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:02.000Z", "uuid": "tr1",
         "toolUseResult": {"status": "async_launched", "isAsync": True},
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_bg",
              "content": [{"type": "text", "text": "Async agent launched successfully."}]}]}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:03.000Z", "uuid": "a2",
         "message": {"id": "msg-2", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "tool_use", "id": "toolu_bash", "name": "Bash",
                                  "input": {"command": "ls"}}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:05.000Z", "uuid": "tr2",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_bash",
              "content": [{"type": "text", "text": "bash output"}]}]}},
        {"type": "user", "timestamp": "2026-01-01T00:00:06.000Z", "uuid": "n1",
         "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": (
             "<task-notification><tool-use-id>toolu_bg</tool-use-id>"
             "<result>Async final result.</result></task-notification>")}},
        {"type": "assistant", "timestamp": "2026-01-01T00:00:07.000Z", "uuid": "a3",
         "message": {"id": "msg-3", "role": "assistant", "model": "claude-test",
                     "content": [{"type": "text", "text": "All done."}]}},
    ]
    turn = hook_module.build_turns(rows)[0]

    hook_module.emit_turn_observations(fake_langfuse, None, turn, None)

    generation_inputs = [
        observation.kwargs.get("input")
        for observation in fake_langfuse.observations
        if observation.as_type == "generation"
    ]
    assert len(generation_inputs) == 3
    final_generation_results = generation_inputs[2]["tool_results"]
    assert [result["tool_use_id"] for result in final_generation_results] == [
        "toolu_bash",
        "toolu_bg",
    ]
    assert final_generation_results[1]["output"] == "Async final result."
