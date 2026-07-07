from __future__ import annotations


def test_build_turns_merges_split_assistant_rows(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    rows = read_fixture_jsonl(fixture_transcript_path("simple_turn"))

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    turn = turns[0]
    assert hook_module.extract_text_from_content(hook_module.get_content_from_row(turn.user_msg)) == "Say hello."
    assert len(turn.assistant_msgs) == 1
    content = hook_module.get_content_from_row(turn.assistant_msgs[0])
    assert [block["text"] for block in content] == ["Hello", "from Claude Code."]
    assert hook_module.get_usage_details_from_row(turn.assistant_msgs[0]) == {
        "input": 10,
        "output": 5,
    }


def test_build_turns_associates_tool_results_with_tool_use(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    rows = read_fixture_jsonl(fixture_transcript_path("tool_turn"))

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    turn = turns[0]
    assert [len(hook_module.get_tool_use_blocks(hook_module.get_content_from_row(row))) for row in turn.assistant_msgs] == [1, 0]
    assert turn.tool_results_by_id["toolu_read_1"] == {
        "content": "# Example",
        "timestamp": "2026-01-01T00:01:02.000Z",
    }
    assert turn.tool_use_timestamps_by_id["toolu_read_1"] == "2026-01-01T00:01:01.000Z"


def test_meta_rows_do_not_start_or_flush_turns(hook_module):
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "message": {"role": "user", "content": "Use a skill."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "message": {
                "id": "msg-skill",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_skill",
                        "name": "Skill",
                        "input": {"skill": "example"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "isMeta": True,
            "sourceToolUseID": "toolu_skill",
            "message": {"role": "user", "content": "Injected skill instructions"},
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:02.000Z",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_skill",
                        "content": "Skill result",
                    }
                ],
            },
        },
    ]

    turns = hook_module.build_turns(rows)

    assert len(turns) == 1
    assert turns[0].injected_by_tool_id == {"toolu_skill": "Injected skill instructions"}
    assert turns[0].tool_results_by_id["toolu_skill"]["content"] == "Skill result"
