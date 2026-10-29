from __future__ import annotations


def test_task_notification_detection_supports_queue_and_origin_rows(hook_module):
    queue_row = {
        "type": "queue-operation",
        "content": "<task-notification><task-id>a1</task-id><result>done</result></task-notification>",
    }
    origin_row = {
        "type": "user",
        "origin": {"kind": "task-notification"},
        "message": {"role": "user", "content": "plain notification text"},
    }

    assert hook_module.is_task_notification_row(queue_row)
    assert hook_module.is_task_notification_row(origin_row)


def test_task_notification_extracts_tool_use_id_task_id_and_result(hook_module):
    row = {
        "type": "user",
        "message": {
            "role": "user",
            "content": (
                "<task-notification><task-id>agent-1</task-id>"
                "<tool-use-id>toolu_agent</tool-use-id>"
                "<result>Agent result</result></task-notification>"
            ),
        },
    }

    assert hook_module.get_task_id_from_task_notification(row) == "agent-1"
    assert hook_module.get_tool_use_id_from_task_notification(row) == "toolu_agent"
    assert hook_module.get_result_from_task_notification(row) == "Agent result"


def test_origin_task_notification_extracts_tool_use_id_after_system_prefix(hook_module):
    row = {
        "type": "user",
        "origin": {"kind": "task-notification"},
        "message": {
            "role": "user",
            "content": (
                "[SYSTEM NOTIFICATION - NOT USER INPUT]\n"
                "This is an automated background-task event.\n\n"
                "<task-notification><task-id>agent-1</task-id>"
                "<tool-use-id>toolu_agent</tool-use-id>"
                "<result>Agent result</result></task-notification>"
            ),
        },
    }

    assert hook_module.is_task_notification_row(row)
    assert hook_module.get_tool_use_id_for_task_notification(row) == "toolu_agent"


def test_non_task_notification_does_not_extract_tool_use_id(hook_module):
    row = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": (
                "The transcript included: <task-notification>"
                "<tool-use-id>toolu_agent</tool-use-id>"
                "</task-notification>"
            ),
        },
    }

    assert not hook_module.is_task_notification_row(row)
    assert hook_module.get_tool_use_id_for_task_notification(row) is None


def test_task_id_fallback_maps_notification_to_tool_use_id(
    hook_module,
    fixture_transcript_path,
    read_fixture_jsonl,
):
    transcript = fixture_transcript_path("task_notification_task_id_fallback")
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    task_id_to_tool_use_id = hook_module.get_task_id_to_tool_use_id(subagents)
    rows = read_fixture_jsonl(transcript)
    notification = next(
        row
        for row in rows
        if hook_module.is_task_notification_row(row)
        and hook_module.get_task_id_from_task_notification(row) == "task-fallback"
    )

    assert hook_module.get_tool_use_id_from_task_notification(notification) is None
    assert hook_module.get_tool_use_id_for_task_notification(
        notification,
        task_id_to_tool_use_id,
    ) == "toolu_task_fallback"

    turns = hook_module.build_turns(rows, task_id_to_tool_use_id)
    assert len(turns) == 1
    result = turns[0].tool_results_by_id["toolu_task_fallback"]
    assert result["final_content"] == "Fallback result through task id."
    assert result["final_timestamp"] == "2026-01-01T00:04:03.000Z"


def _user_row(uuid, text, timestamp):
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": text},
    }


def _assistant_row(uuid, message_id, content, timestamp):
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "uuid": uuid,
        "message": {
            "id": message_id,
            "role": "assistant",
            "model": "claude-test",
            "content": content,
        },
    }


def _background_tool_result_row(tool_use_id, task_id, timestamp):
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": f"result-{tool_use_id}",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [
                        {
                            "type": "text",
                            "text": f"Command running in background with ID: {task_id}.",
                        }
                    ],
                }
            ],
        },
        "toolUseResult": {"backgroundTaskId": task_id},
    }


def _notification_attachment_row(task_id, result, timestamp):
    return {
        "type": "attachment",
        "timestamp": timestamp,
        "uuid": f"attachment-{task_id}",
        "attachment": {
            "type": "queued_command",
            "commandMode": "task-notification",
            "prompt": (
                f"<task-notification><task-id>{task_id}</task-id>"
                f"<result>{result}</result></task-notification>"
            ),
        },
    }


def test_queued_command_attachment_is_a_task_notification(hook_module):
    row = _notification_attachment_row("task-1", "Background result.", "2026-01-01T00:00:03.000Z")

    assert hook_module.is_task_notification_row(row)
    assert hook_module.get_task_id_from_task_notification(row) == "task-1"
    assert hook_module.get_result_from_task_notification(row) == "Background result."


def test_other_attachment_rows_are_not_task_notifications(hook_module):
    row = {
        "type": "attachment",
        "attachment": {"type": "skill_listing", "content": "- review: Review a pull request"},
    }

    assert not hook_module.is_task_notification_row(row)


def test_background_task_id_maps_to_launching_tool_use_id(hook_module):
    rows = [
        _user_row("user-1", "Run the command in the background.", "2026-01-01T00:00:00.000Z"),
        _assistant_row(
            "assistant-1",
            "msg-1",
            [
                {
                    "type": "tool_use",
                    "id": "toolu_bg",
                    "name": "Bash",
                    "input": {"command": "sleep 150", "run_in_background": True},
                }
            ],
            "2026-01-01T00:00:01.000Z",
        ),
        _background_tool_result_row("toolu_bg", "task-1", "2026-01-01T00:00:02.000Z"),
        _notification_attachment_row("task-1", "Background result.", "2026-01-01T00:00:03.000Z"),
        _assistant_row(
            "assistant-2",
            "msg-2",
            [{"type": "text", "text": "The command finished."}],
            "2026-01-01T00:00:04.000Z",
        ),
    ]

    mapping = hook_module.get_background_task_id_to_tool_use_id(rows)
    assert mapping == {"task-1": "toolu_bg"}

    turns = hook_module.build_turns(rows, mapping)
    assert len(turns) == 1
    result = turns[0].tool_results_by_id["toolu_bg"]
    assert result["final_content"] == "Background result."
    assert result["final_timestamp"] == "2026-01-01T00:00:03.000Z"


def test_mid_turn_attachment_resolves_through_transcript_assembly(hook_module, tmp_path):
    import json

    rows = [
        _user_row("user-1", "Run the command in the background.", "2026-01-01T00:00:00.000Z"),
        _assistant_row(
            "assistant-1",
            "msg-1",
            [
                {
                    "type": "tool_use",
                    "id": "toolu_bg",
                    "name": "Bash",
                    "input": {"command": "sleep 150", "run_in_background": True},
                }
            ],
            "2026-01-01T00:00:01.000Z",
        ),
        _background_tool_result_row("toolu_bg", "task-1", "2026-01-01T00:00:02.000Z"),
        _notification_attachment_row("task-1", "Background result.", "2026-01-01T00:00:03.000Z"),
        _assistant_row(
            "assistant-2",
            "msg-2",
            [{"type": "text", "text": "The command finished."}],
            "2026-01-01T00:00:04.000Z",
        ),
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    turns, _ = hook_module.get_new_turns_from_transcript(
        transcript,
        hook_module.SessionState(),
        flush_deferred_agent_turns=True,
    )

    assert len(turns) == 1
    assert turns[0].tool_results_by_id["toolu_bg"]["final_content"] == "Background result."
