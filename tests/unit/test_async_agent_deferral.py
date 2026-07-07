from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def make_user_row(uuid: str, text: str, timestamp: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "origin": {"kind": "human"},
        "message": {"role": "user", "content": text},
    }


def make_assistant_row(uuid: str, message_id: str, content: list[dict[str, Any]], timestamp: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "uuid": uuid,
        "message": {"id": message_id, "role": "assistant", "model": "claude-test", "content": content},
    }


def make_async_launch_result_row(uuid: str, tool_use_id: str, timestamp: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Async agent launched successfully.\n"
                                "agentId: agent-test\n"
                                "output_file: /tmp/agent-test.txt\n"
                                "You will be notified automatically when the agent completes."
                            ),
                        }
                    ],
                }
            ],
        },
    }


def make_notification_row(uuid: str, tool_use_id: str, result: str, timestamp: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "origin": {"kind": "task-notification"},
        "message": {
            "role": "user",
            "content": (
                f"<task-notification><tool-use-id>{tool_use_id}</tool-use-id>"
                f"<result>{result}</result></task-notification>"
            ),
        },
    }


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def launch_turn_rows(tool_use_id: str = "toolu_bg") -> list[dict[str, Any]]:
    return [
        make_user_row("user-1", "Start a background agent.", "2026-01-01T00:00:00.000Z"),
        make_assistant_row(
            "assistant-1",
            "msg-1",
            [{"type": "tool_use", "id": tool_use_id, "name": "Agent",
              "input": {"description": "Research", "prompt": "Research slowly"}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_async_launch_result_row("tool-result-1", tool_use_id, "2026-01-01T00:00:02.000Z"),
        make_assistant_row(
            "assistant-2",
            "msg-2",
            [{"type": "text", "text": "The agent is running in the background."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ]


def test_completed_async_agent_turn_is_ready_to_emit(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("async_agent_completed")
    state = hook_module.SessionState()
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, subagents)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state, subagents)

    assert len(turns) == 1
    assert len(turns_to_emit) == 1
    assert state.pending_agent_turns == []
    result = turns[0].tool_results_by_id["toolu_agent_complete"]
    assert result["final_content"] == "Subagent summary is ready."


def test_uncompleted_async_agent_turn_is_deferred_until_flush(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("async_agent_deferred")
    state = hook_module.SessionState()
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, subagents)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state, subagents)

    assert turns_to_emit == []
    assert len(state.pending_agent_turns) == 1
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_deferred"]

    flushed_turns, state = hook_module.get_new_turns_from_transcript(
        transcript,
        state,
        subagents,
        flush_deferred_agent_turns=True,
    )
    flushed_to_emit = hook_module.get_turns_to_emit(
        flushed_turns,
        state,
        subagents,
        flush_deferred_agent_turns=True,
    )

    assert len(flushed_to_emit) == 1
    assert state.pending_agent_turns == []


def test_turn_waiting_on_multiple_agents_resolves_only_after_all_notifications(hook_module):
    deferred_rows = launch_turn_rows("toolu_agent_a")
    state = hook_module.SessionState(
        pending_agent_turns=[
            {
                "pending_tool_use_ids": ["toolu_agent_a", "toolu_agent_b"],
                "rows": deferred_rows,
            },
        ],
    )

    first_notification = make_notification_row(
        "notif-a", "toolu_agent_a", "Result A", "2026-01-01T00:01:00.000Z"
    )
    resolved, remaining = hook_module.resolve_deferred_agent_turns([first_notification], state)

    # One of two agents finished: the notification is captured, but the turn
    # keeps waiting for the second agent instead of being emitted half-done.
    assert resolved == []
    assert remaining == []
    assert len(state.pending_agent_turns) == 1
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_b"]
    assert state.pending_agent_turns[0]["resolved_tool_use_ids"] == ["toolu_agent_a"]
    assert state.pending_agent_turns[0]["rows"][-1] is first_notification

    second_notification = make_notification_row(
        "notif-b", "toolu_agent_b", "Result B", "2026-01-01T00:02:00.000Z"
    )
    resolved, remaining = hook_module.resolve_deferred_agent_turns([second_notification], state)

    assert remaining == []
    assert state.pending_agent_turns == []
    assert len(resolved) == 1
    assert resolved[0] == deferred_rows
    assert resolved[0][-2:] == [first_notification, second_notification]


def test_duplicate_notifications_for_same_agent_are_routed_to_the_deferred_turn(hook_module):
    deferred_rows = launch_turn_rows("toolu_agent_a")
    state = hook_module.SessionState(
        pending_agent_turns=[
            {
                "pending_tool_use_ids": ["toolu_agent_a"],
                "rows": deferred_rows,
            },
        ],
    )
    notifications = [
        make_notification_row("notif-1", "toolu_agent_a", "Result", "2026-01-01T00:01:00.000Z"),
        make_notification_row("notif-2", "toolu_agent_a", "Result (final)", "2026-01-01T00:01:01.000Z"),
    ]

    resolved, remaining = hook_module.resolve_deferred_agent_turns(notifications, state)

    # Real transcripts often carry two notification rows per task; the second
    # one must follow the first into the deferred turn instead of leaking into
    # the current batch as an orphan row.
    assert remaining == []
    assert state.pending_agent_turns == []
    assert len(resolved) == 1
    assert resolved[0][-2:] == notifications

    turns = hook_module.build_turns(resolved[0])
    assert len(turns) == 1
    assert turns[0].tool_results_by_id["toolu_agent_a"]["final_content"] == "Result (final)"


def test_resolve_ignores_non_notification_tool_use_xml(hook_module):
    deferred_rows = [{"uuid": "deferred-row"}]
    current_rows = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": (
                    "Quoted notification: <task-notification>"
                    "<tool-use-id>toolu_agent_a</tool-use-id>"
                    "</task-notification>"
                ),
            },
        },
    ]
    state = hook_module.SessionState(
        pending_agent_turns=[
            {
                "pending_tool_use_ids": ["toolu_agent_a"],
                "rows": deferred_rows,
            },
        ],
    )

    resolved, remaining = hook_module.resolve_deferred_agent_turns(current_rows, state)

    assert resolved == []
    assert remaining == current_rows
    assert state.pending_agent_turns == [
        {
            "pending_tool_use_ids": ["toolu_agent_a"],
            "rows": deferred_rows,
        },
    ]


def test_multi_agent_turn_is_stored_once_with_all_waiting_tool_ids(hook_module):
    rows = [{"uuid": "user-row"}, {"uuid": "assistant-row"}]
    turn = hook_module.Turn(
        user_msg=rows[0],
        assistant_msgs=[
            {
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_agent_a", "name": "Agent"},
                        {"type": "tool_use", "id": "toolu_agent_b", "name": "Agent"},
                    ],
                },
            },
        ],
        tool_results_by_id={
            "toolu_agent_a": {
                "content": "Async agent launched successfully. agentId: agent-a output_file: /tmp/a You will be notified automatically"
            },
            "toolu_agent_b": {
                "content": "Async agent launched successfully. agentId: agent-b output_file: /tmp/b You will be notified automatically"
            },
        },
        tool_use_timestamps_by_id={},
        injected_by_tool_id={},
        rows=rows,
    )
    state = hook_module.SessionState()

    turns_to_emit = hook_module.get_turns_to_emit([turn], state)

    assert turns_to_emit == []
    assert len(state.pending_agent_turns) == 1
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_a", "toolu_agent_b"]
    assert state.pending_agent_turns[0]["rows"] == rows


def test_mid_turn_notification_does_not_corrupt_the_surrounding_turn(hook_module, tmp_path):
    """Regression: resolving a deferred turn used to splice its rows into the
    middle of the current batch, truncating the current turn at the splice
    point and gluing its remaining assistant rows onto the rebuilt turn."""
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    # Hook run 1: turn 1 launches an async agent and ends -> deferred.
    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_agent_turns) == 1

    # Hook run 2: turn 2 is mid-flight when the notification lands between
    # two of its assistant messages (the normal real-world shape).
    append_jsonl(transcript, [
        make_user_row("user-2", "Next question.", "2026-01-01T00:05:00.000Z"),
        make_assistant_row(
            "assistant-3", "msg-3",
            [{"type": "text", "text": "Working on it."}],
            "2026-01-01T00:05:01.000Z",
        ),
        make_notification_row("notif-1", "toolu_bg", "Background result.", "2026-01-01T00:05:02.000Z"),
        make_assistant_row(
            "assistant-4", "msg-4",
            [{"type": "text", "text": "Here is the answer."}],
            "2026-01-01T00:05:03.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert state.pending_agent_turns == []
    assert len(turns_to_emit) == 2

    resolved_turn, current_turn = turns_to_emit
    # The deferred turn is rebuilt intact, with the notification result attached.
    assert resolved_turn.user_msg["uuid"] == "user-1"
    assert [m["message"]["id"] for m in resolved_turn.assistant_msgs] == ["msg-1", "msg-2"]
    assert resolved_turn.tool_results_by_id["toolu_bg"]["final_content"] == "Background result."
    # The current turn keeps ALL of its assistant messages.
    assert current_turn.user_msg["uuid"] == "user-2"
    assert [m["message"]["id"] for m in current_turn.assistant_msgs] == ["msg-3", "msg-4"]


def test_notification_before_first_assistant_row_does_not_drop_the_turn(hook_module, tmp_path):
    """Regression: a notification arriving between a user prompt and its first
    assistant row used to erase that turn entirely (never traced)."""
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_user_row("user-2", "Next question.", "2026-01-01T00:05:00.000Z"),
        make_notification_row("notif-1", "toolu_bg", "Background result.", "2026-01-01T00:05:01.000Z"),
        make_assistant_row(
            "assistant-3", "msg-3",
            [{"type": "text", "text": "Here is the answer."}],
            "2026-01-01T00:05:02.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert len(turns_to_emit) == 2
    resolved_turn, current_turn = turns_to_emit
    assert resolved_turn.user_msg["uuid"] == "user-1"
    assert resolved_turn.tool_results_by_id["toolu_bg"]["final_content"] == "Background result."
    assert current_turn.user_msg["uuid"] == "user-2"
    assert [m["message"]["id"] for m in current_turn.assistant_msgs] == ["msg-3"]
