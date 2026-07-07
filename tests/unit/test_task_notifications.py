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
