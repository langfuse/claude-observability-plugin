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


def make_agent_result_row(
    uuid: str,
    tool_use_id: str,
    text: str,
    timestamp: str,
    tool_use_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": text}],
                }
            ],
        },
    }
    if tool_use_result is not None:
        row["toolUseResult"] = tool_use_result
    return row


def make_async_launch_result_row(uuid: str, tool_use_id: str, timestamp: str) -> dict[str, Any]:
    return make_agent_result_row(
        uuid,
        tool_use_id,
        (
            "Async agent launched successfully.\n"
            "agentId: agent-test\n"
            "output_file: /tmp/agent-test.txt\n"
            "You will be notified automatically when the agent completes."
        ),
        timestamp,
        tool_use_result={"status": "async_launched", "isAsync": True, "agentId": "agent-test"},
    )


def make_notification_row(
    uuid: str,
    tool_use_id: str,
    result: str,
    timestamp: str,
    task_id: str | None = None,
) -> dict[str, Any]:
    task_id_xml = f"<task-id>{task_id}</task-id>" if task_id else ""
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "origin": {"kind": "task-notification"},
        "message": {
            "role": "user",
            "content": (
                f"<task-notification>{task_id_xml}<tool-use-id>{tool_use_id}</tool-use-id>"
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
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert turns == []
    assert turns_to_emit == []
    assert state.open_turn_rows
    assert state.pending_agent_turns == []

    turns, state = hook_module.get_new_turns_from_transcript(
        transcript,
        state,
        subagents,
        flush_deferred_agent_turns=True,
    )
    turns_to_emit = hook_module.get_turns_to_emit(
        turns,
        state,
        flush_deferred_agent_turns=True,
    )

    assert len(turns_to_emit) == 1
    assert state.open_turn_rows == []
    result = turns_to_emit[0].tool_results_by_id["toolu_agent_complete"]
    assert result["final_content"] == "Subagent summary is ready."


def test_uncompleted_async_agent_turn_is_deferred_until_flush(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("async_agent_deferred")
    state = hook_module.SessionState()
    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, subagents)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert turns_to_emit == []
    assert state.open_turn_rows
    assert state.pending_agent_turns == []

    flushed_turns, state = hook_module.get_new_turns_from_transcript(
        transcript,
        state,
        subagents,
        flush_deferred_agent_turns=True,
    )
    flushed_to_emit = hook_module.get_turns_to_emit(
        flushed_turns,
        state,
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


def test_adjacent_parallel_agent_notifications_are_not_guessed_to_match(hook_module):
    deferred_rows = launch_turn_rows("toolu_agent_a")
    state = hook_module.SessionState(
        pending_agent_turns=[
            {
                "pending_tool_use_ids": ["toolu_agent_a", "toolu_agent_b"],
                "rows": deferred_rows,
            },
        ],
    )
    notification_a_without_mapping = make_task_id_notification_row(
        "notif-a", "agent-a", "Result A", "2026-01-01T00:01:00.000Z"
    )
    notification_b = make_notification_row(
        "notif-b", "toolu_agent_b", "Result B", "2026-01-01T00:01:01.000Z"
    )

    resolved, remaining = hook_module.resolve_deferred_agent_turns(
        [notification_a_without_mapping, notification_b],
        state,
    )

    assert resolved == []
    assert remaining == []
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_agent_a"]
    assert state.pending_agent_turns[0]["rows"][-1] == notification_b
    assert state.pending_task_notifications == [notification_a_without_mapping]

    resolved, remaining = hook_module.resolve_deferred_agent_turns(
        [],
        state,
        {"agent-a": "toolu_agent_a"},
    )

    assert remaining == []
    assert state.pending_agent_turns == []
    assert len(resolved) == 1
    assert resolved[0][-2:] == [notification_a_without_mapping, notification_b]


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

    # Hook run 1: turn 1 launches an async agent and remains open.
    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert state.open_turn_rows
    assert state.pending_agent_turns == []

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
    assert len(turns_to_emit) == 1

    resolved_turn = turns_to_emit[0]
    # The older turn is rebuilt intact, with the notification result attached.
    assert resolved_turn.user_msg["uuid"] == "user-1"
    assert [m["message"]["id"] for m in resolved_turn.assistant_msgs] == ["msg-1", "msg-2"]
    assert resolved_turn.tool_results_by_id["toolu_bg"]["final_content"] == "Background result."

    # The current turn stays open until the next real user row, then keeps ALL
    # of the assistant messages that surrounded the notification.
    append_jsonl(transcript, [
        make_user_row("user-3", "One more question.", "2026-01-01T00:05:04.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)
    assert len(turns_to_emit) == 1
    current_turn = turns_to_emit[0]
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

    assert len(turns_to_emit) == 1
    resolved_turn = turns_to_emit[0]
    assert resolved_turn.user_msg["uuid"] == "user-1"
    assert resolved_turn.tool_results_by_id["toolu_bg"]["final_content"] == "Background result."

    append_jsonl(transcript, [
        make_user_row("user-3", "Another question.", "2026-01-01T00:05:03.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)
    assert len(turns_to_emit) == 1
    current_turn = turns_to_emit[0]
    assert current_turn.user_msg["uuid"] == "user-2"
    assert [m["message"]["id"] for m in current_turn.assistant_msgs] == ["msg-3"]


def test_closed_deferred_agent_and_open_current_turn_coexist(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    # The next human row closes turn 1 before its async notification, so the
    # existing agent-specific mechanism defers it while turn 2 stays open.
    append_jsonl(transcript, [
        make_user_row("user-2", "Work on turn two.", "2026-01-01T00:05:00.000Z"),
        make_assistant_row(
            "assistant-3", "msg-3", [{"type": "text", "text": "Turn two starts."}],
            "2026-01-01T00:05:01.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_agent_turns) == 1
    assert state.open_turn_rows

    # The old turn resolves between ordinary continuation rows for the open
    # current turn. They must remain isolated from each other.
    append_jsonl(transcript, [
        make_assistant_row(
            "assistant-4", "msg-4", [{"type": "text", "text": "Still on turn two."}],
            "2026-01-01T00:05:02.000Z",
        ),
        make_notification_row(
            "notif-bg", "toolu_bg", "Background result.", "2026-01-01T00:05:03.000Z"
        ),
        make_assistant_row(
            "assistant-5", "msg-5", [{"type": "text", "text": "Turn two continues."}],
            "2026-01-01T00:05:04.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    assert emitted[0].user_msg["uuid"] == "user-1"
    assert emitted[0].tool_results_by_id["toolu_bg"]["final_content"] == "Background result."
    assert state.pending_agent_turns == []
    all_emitted = list(emitted)

    append_jsonl(transcript, [
        make_user_row("user-3", "Close turn two.", "2026-01-01T00:05:05.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    assert emitted[0].user_msg["uuid"] == "user-2"
    assert [row["message"]["id"] for row in emitted[0].assistant_msgs] == [
        "msg-3", "msg-4", "msg-5",
    ]
    all_emitted.extend(emitted)
    emitted_row_uuid_sets = [
        {row["uuid"] for row in turn.rows if row.get("uuid")}
        for turn in all_emitted
    ]
    assert emitted_row_uuid_sets[0].isdisjoint(emitted_row_uuid_sets[1])
    all_row_uuids = [
        row["uuid"]
        for turn in all_emitted
        for row in turn.rows
        if row.get("uuid")
    ]
    assert len(all_row_uuids) == len(set(all_row_uuids))


def test_same_batch_notification_is_routed_before_its_later_user_boundary(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()
    append_jsonl(transcript, [
        *launch_turn_rows("toolu_bg"),
        make_assistant_row(
            "assistant-between", "msg-between",
            [{"type": "text", "text": "Continuation before the next prompt."}],
            "2026-01-01T00:04:59.000Z",
        ),
        make_user_row("user-2", "Second prompt.", "2026-01-01T00:05:00.000Z"),
        make_notification_row(
            "notif-bg", "toolu_bg", "Background result.", "2026-01-01T00:05:01.000Z"
        ),
        make_assistant_row(
            "assistant-3", "msg-3", [{"type": "text", "text": "Second answer."}],
            "2026-01-01T00:05:02.000Z",
        ),
    ])

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    first_turn = emitted[0]
    assert first_turn.user_msg["uuid"] == "user-1"
    assert first_turn.tool_results_by_id["toolu_bg"]["final_content"] == "Background result."
    assert [row["uuid"] for row in first_turn.rows if row.get("uuid")] == [
        "user-1",
        "assistant-1",
        "tool-result-1",
        "assistant-2",
        "assistant-between",
        "notif-bg",
    ]
    assert state.pending_agent_turns == []

    append_jsonl(transcript, [
        make_user_row("user-3", "Third prompt.", "2026-01-01T00:05:03.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    assert emitted[0].user_msg["uuid"] == "user-2"
    assert [row["message"]["id"] for row in emitted[0].assistant_msgs] == ["msg-3"]
    assert "toolu_bg" not in emitted[0].tool_results_by_id


def test_late_duplicate_notification_does_not_contaminate_unrelated_open_turn(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_notification_row(
            "notif-1", "toolu_bg", "Background result.", "2026-01-01T00:04:59.000Z"
        ),
        make_user_row("user-2", "Second prompt.", "2026-01-01T00:05:00.000Z"),
        make_assistant_row(
            "assistant-3", "msg-3", [{"type": "text", "text": "Second answer."}],
            "2026-01-01T00:05:01.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)
    assert len(emitted) == 1
    assert emitted[0].user_msg["uuid"] == "user-1"

    append_jsonl(transcript, [
        make_notification_row(
            "notif-2", "toolu_bg", "Duplicate result.", "2026-01-01T00:05:02.000Z"
        ),
        make_user_row("user-3", "Third prompt.", "2026-01-01T00:05:03.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    assert emitted[0].user_msg["uuid"] == "user-2"
    assert all(row.get("uuid") != "notif-2" for row in emitted[0].rows)
    assert "toolu_bg" not in emitted[0].tool_results_by_id


def test_ordinary_turn_spanning_multiple_stop_firings_is_not_dropped(hook_module, tmp_path):
    """A turn with no async agent involved at all can still legitimately span
    more than one Stop firing. None of its content should be lost."""
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, [
        make_user_row(
            "user-1",
            "Do the first thing, then the second thing.",
            "2026-01-01T00:00:00.000Z",
        ),
        make_assistant_row(
            "assistant-1",
            "msg-1",
            [{"type": "tool_use", "id": "toolu_1", "name": "Bash",
              "input": {"command": "echo one"}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_agent_result_row(
            "tool-result-1", "toolu_1", "one", "2026-01-01T00:00:02.000Z"
        ),
        make_assistant_row(
            "assistant-2",
            "msg-2",
            [{"type": "text", "text": "Done with the first thing."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_assistant_row(
            "assistant-3",
            "msg-3",
            [{"type": "tool_use", "id": "toolu_2", "name": "Bash",
              "input": {"command": "echo two"}}],
            "2026-01-01T00:00:04.000Z",
        ),
        make_agent_result_row(
            "tool-result-2", "toolu_2", "two", "2026-01-01T00:00:05.000Z"
        ),
        make_assistant_row(
            "assistant-4",
            "msg-4",
            [{"type": "text", "text": "Done with the second thing too."}],
            "2026-01-01T00:00:06.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_user_row(
            "user-2", "Great, now do a third thing.", "2026-01-01T00:00:07.000Z"
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert len(turns_to_emit) == 1
    closed_turn = turns_to_emit[0]
    assert closed_turn.user_msg["uuid"] == "user-1"
    assert [m["message"]["id"] for m in closed_turn.assistant_msgs] == [
        "msg-1", "msg-2", "msg-3", "msg-4",
    ]
    assert closed_turn.tool_results_by_id["toolu_1"]["content"] == [
        {"type": "text", "text": "one"},
    ]
    assert closed_turn.tool_results_by_id["toolu_2"]["content"] == [
        {"type": "text", "text": "two"},
    ]
    row_uuids = [row["uuid"] for row in closed_turn.rows if row.get("uuid")]
    assert len(row_uuids) == len(set(row_uuids))


def test_ordinary_turn_accumulates_across_four_stop_firings(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()
    emitted = []

    batches = [
        [
            make_user_row("user-1", "Keep working.", "2026-01-01T00:00:00.000Z"),
            make_assistant_row(
                "assistant-1", "msg-1", [{"type": "text", "text": "Step one."}],
                "2026-01-01T00:00:01.000Z",
            ),
        ],
        [
            make_assistant_row(
                "assistant-2", "msg-2",
                [{"type": "tool_use", "id": "toolu_many", "name": "Bash", "input": {}}],
                "2026-01-01T00:00:02.000Z",
            ),
        ],
        [
            make_agent_result_row(
                "tool-result-many", "toolu_many", "result", "2026-01-01T00:00:03.000Z"
            ),
            make_assistant_row(
                "assistant-3", "msg-3", [{"type": "text", "text": "Step three."}],
                "2026-01-01T00:00:04.000Z",
            ),
        ],
        [
            make_assistant_row(
                "assistant-4", "msg-4", [{"type": "text", "text": "Step four."}],
                "2026-01-01T00:00:05.000Z",
            ),
        ],
    ]

    for batch in batches:
        append_jsonl(transcript, batch)
        turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
        emitted.extend(hook_module.get_turns_to_emit(turns, state))
        assert emitted == []

    append_jsonl(transcript, [
        make_user_row("user-2", "Now stop.", "2026-01-01T00:00:06.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted.extend(hook_module.get_turns_to_emit(turns, state))

    assert [turn.user_msg["uuid"] for turn in emitted] == ["user-1"]
    closed_turn = emitted[0]
    assert [row["message"]["id"] for row in closed_turn.assistant_msgs] == [
        "msg-1", "msg-2", "msg-3", "msg-4",
    ]
    assert closed_turn.tool_results_by_id["toolu_many"]["content"] == [
        {"type": "text", "text": "result"},
    ]
    row_uuids = [row["uuid"] for row in closed_turn.rows if row.get("uuid")]
    assert len(row_uuids) == len(set(row_uuids))


def test_async_resolution_then_ordinary_continuation_stays_in_one_open_turn(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, [
        make_user_row("user-1", "Do all the work.", "2026-01-01T00:00:00.000Z"),
        make_assistant_row(
            "assistant-1", "msg-1",
            [{"type": "tool_use", "id": "toolu_sync", "name": "Bash", "input": {}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_agent_result_row(
            "tool-result-sync", "toolu_sync", "sync", "2026-01-01T00:00:02.000Z"
        ),
        make_assistant_row(
            "assistant-2", "msg-2", [{"type": "text", "text": "First part done."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_assistant_row(
            "assistant-3", "msg-3",
            [{"type": "tool_use", "id": "toolu_bg", "name": "Agent", "input": {}}],
            "2026-01-01T00:00:04.000Z",
        ),
        make_async_launch_result_row(
            "tool-result-bg", "toolu_bg", "2026-01-01T00:00:05.000Z"
        ),
        make_assistant_row(
            "assistant-4", "msg-4", [{"type": "text", "text": "Agent is running."}],
            "2026-01-01T00:00:06.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_notification_row(
            "notif-bg", "toolu_bg", "Agent result.", "2026-01-01T00:00:07.000Z"
        ),
        make_assistant_row(
            "assistant-5", "msg-5",
            [{"type": "tool_use", "id": "toolu_after", "name": "Bash", "input": {}}],
            "2026-01-01T00:00:08.000Z",
        ),
        make_agent_result_row(
            "tool-result-after", "toolu_after", "after", "2026-01-01T00:00:09.000Z"
        ),
        make_assistant_row(
            "assistant-6", "msg-6", [{"type": "text", "text": "Everything is done."}],
            "2026-01-01T00:00:10.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_user_row("user-2", "Thanks.", "2026-01-01T00:00:11.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    turn = emitted[0]
    assert turn.user_msg["uuid"] == "user-1"
    assert [row["message"]["id"] for row in turn.assistant_msgs] == [
        "msg-1", "msg-2", "msg-3", "msg-4", "msg-5", "msg-6",
    ]
    assert turn.tool_results_by_id["toolu_bg"]["final_content"] == "Agent result."
    assert turn.tool_results_by_id["toolu_after"]["content"] == [
        {"type": "text", "text": "after"},
    ]
    row_uuids = [row["uuid"] for row in turn.rows if row.get("uuid")]
    assert len(row_uuids) == len(set(row_uuids))


def test_user_only_tail_is_retained_until_its_first_assistant_row(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()
    append_jsonl(transcript, [
        make_user_row("user-1", "Wait for permission.", "2026-01-01T00:00:00.000Z"),
    ])

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert [row["uuid"] for row in state.open_turn_rows] == ["user-1"]

    append_jsonl(transcript, [
        make_assistant_row(
            "assistant-1", "msg-1", [{"type": "text", "text": "Permission granted."}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_user_row("user-2", "Continue.", "2026-01-01T00:00:02.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    assert emitted[0].user_msg["uuid"] == "user-1"
    assert [row["message"]["id"] for row in emitted[0].assistant_msgs] == ["msg-1"]


def test_session_end_flushes_an_open_ordinary_turn_exactly_once(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()
    append_jsonl(transcript, [
        make_user_row("user-1", "Finish before exit.", "2026-01-01T00:00:00.000Z"),
        make_assistant_row(
            "assistant-1", "msg-1",
            [{"type": "tool_use", "id": "toolu_exit", "name": "Bash", "input": {}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_agent_result_row(
            "tool-result-exit", "toolu_exit", "done", "2026-01-01T00:00:02.000Z"
        ),
        make_assistant_row(
            "assistant-2", "msg-2", [{"type": "text", "text": "Finished."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ])

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert state.open_turn_rows

    turns, state = hook_module.get_new_turns_from_transcript(
        transcript, state, flush_deferred_agent_turns=True
    )
    emitted = hook_module.get_turns_to_emit(
        turns, state, flush_deferred_agent_turns=True
    )
    assert len(emitted) == 1
    assert [row["message"]["id"] for row in emitted[0].assistant_msgs] == ["msg-1", "msg-2"]
    assert emitted[0].tool_results_by_id["toolu_exit"]["content"] == [
        {"type": "text", "text": "done"},
    ]
    assert state.open_turn_rows == []

    turns, state = hook_module.get_new_turns_from_transcript(
        transcript, state, flush_deferred_agent_turns=True
    )
    assert hook_module.get_turns_to_emit(
        turns, state, flush_deferred_agent_turns=True
    ) == []


def test_disconnected_human_row_cannot_erase_the_accumulated_open_turn(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()
    first_batch = [
        make_user_row("user-1", "Work on the real branch.", "2026-01-01T00:00:00.000Z"),
        make_assistant_row(
            "assistant-1", "msg-1",
            [{"type": "tool_use", "id": "toolu_real", "name": "Bash", "input": {}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_agent_result_row(
            "tool-result-real", "toolu_real", "real", "2026-01-01T00:00:02.000Z"
        ),
        make_assistant_row(
            "assistant-2", "msg-2", [{"type": "text", "text": "Captured prefix."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ]
    first_batch[1]["parentUuid"] = "user-1"
    first_batch[2]["parentUuid"] = "assistant-1"
    first_batch[3]["parentUuid"] = "tool-result-real"
    append_jsonl(transcript, first_batch)
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    stray = make_user_row(
        "user-foreign", "Unrelated branch.", "2026-01-01T00:00:04.000Z"
    )
    stray["parentUuid"] = "foreign-parent"
    continuation = make_assistant_row(
        "assistant-3", "msg-3", [{"type": "text", "text": "Later content."}],
        "2026-01-01T00:00:05.000Z",
    )
    continuation["parentUuid"] = "assistant-2"
    append_jsonl(transcript, [stray, continuation])

    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    # The conservative fallback is allowed to close the real turn early, but
    # the disconnected row must never erase or shorten its captured prefix.
    assert len(emitted) == 1
    real_turn = emitted[0]
    assert real_turn.user_msg["uuid"] == "user-1"
    assert [row["message"]["id"] for row in real_turn.assistant_msgs] == ["msg-1", "msg-2"]
    assert real_turn.tool_results_by_id["toolu_real"]["content"] == [
        {"type": "text", "text": "real"},
    ]
    row_uuids = [row["uuid"] for row in real_turn.rows if row.get("uuid")]
    assert len(row_uuids) == len(set(row_uuids))

    append_jsonl(transcript, [
        make_user_row("user-2", "Back to the real branch.", "2026-01-01T00:00:06.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    stray_turns = hook_module.get_turns_to_emit(turns, state)

    assert len(stray_turns) == 1
    assert stray_turns[0].user_msg["uuid"] == "user-foreign"
    assert [row["message"]["id"] for row in stray_turns[0].assistant_msgs] == ["msg-3"]
    stray_row_uuids = {
        row["uuid"] for row in stray_turns[0].rows if row.get("uuid")
    }
    assert set(row_uuids).isdisjoint(stray_row_uuids)


def test_open_turn_state_is_backward_compatible_and_round_trips(hook_module):
    old_global_state = {
        "old": {
            "offset": 12,
            "buffer": "",
            "turn_count": 3,
            "pending_agent_turns": [],
            "pending_task_notifications": [],
        },
        "malformed": {
            "open_turn_rows": {"not": "a list"},
            "pending_task_notification_contexts": {"not": "a list"},
        },
    }

    assert hook_module.get_session_state(old_global_state, "old").open_turn_rows == []
    assert hook_module.get_session_state(old_global_state, "malformed").open_turn_rows == []
    assert hook_module.get_session_state(
        old_global_state, "old"
    ).pending_task_notification_contexts == []
    assert hook_module.get_session_state(
        old_global_state, "malformed"
    ).pending_task_notification_contexts == []

    open_row = make_user_row("user-1", "Still open.", "2026-01-01T00:00:00.000Z")
    state = hook_module.SessionState(open_turn_rows=[open_row])
    serialized: dict[str, Any] = {}
    hook_module.update_session_state(serialized, "key", state)

    assert hook_module.get_session_state(serialized, "key").open_turn_rows == [open_row]


def sync_agent_turn_rows(tool_use_id: str = "toolu_sync", result_text: str = "Here is the final research report.") -> list[dict[str, Any]]:
    return [
        make_user_row("user-1", "Run a subagent for research.", "2026-01-01T00:00:00.000Z"),
        make_assistant_row(
            "assistant-1",
            "msg-1",
            [{"type": "tool_use", "id": tool_use_id, "name": "Agent",
              "input": {"description": "Research", "prompt": "Research now"}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_agent_result_row(
            "tool-result-1",
            tool_use_id,
            result_text,
            "2026-01-01T00:00:02.000Z",
            tool_use_result={"status": "completed", "agentId": "agent-sync", "totalDurationMs": 1000},
        ),
        make_assistant_row(
            "assistant-2",
            "msg-2",
            [{"type": "text", "text": "Summary of the research."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ]


def test_sync_agent_turn_is_emitted_immediately_despite_subagent_transcript(hook_module):
    """Regression: a subagent transcript on disk used to defer the turn, but
    sync agents never produce the task notification that releases it, so the
    turn was stuck until SessionEnd (or lost entirely in killed sessions)."""
    turns = hook_module.build_turns(sync_agent_turn_rows())
    state = hook_module.SessionState()

    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert len(turns_to_emit) == 1
    assert state.pending_agent_turns == []


def test_structured_async_marker_defers_even_if_launch_text_changes(hook_module):
    rows = [
        make_user_row("user-1", "Start a background agent.", "2026-01-01T00:00:00.000Z"),
        make_assistant_row(
            "assistant-1",
            "msg-1",
            [{"type": "tool_use", "id": "toolu_bg", "name": "Agent", "input": {}}],
            "2026-01-01T00:00:01.000Z",
        ),
        make_agent_result_row(
            "tool-result-1",
            "toolu_bg",
            "Background agent started.",  # no recognizable launch prose
            "2026-01-01T00:00:02.000Z",
            tool_use_result={"status": "async_launched", "isAsync": True},
        ),
        make_assistant_row(
            "assistant-2",
            "msg-2",
            [{"type": "text", "text": "Working in the background."}],
            "2026-01-01T00:00:03.000Z",
        ),
    ]
    turns = hook_module.build_turns(rows)
    state = hook_module.SessionState()

    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert turns_to_emit == []
    assert len(state.pending_agent_turns) == 1
    assert state.pending_agent_turns[0]["pending_tool_use_ids"] == ["toolu_bg"]


def test_structured_completed_marker_suppresses_launch_text_false_positive(hook_module):
    # A sync agent whose RESULT quotes the launch prose (e.g. it inspected
    # another transcript) must not be mistaken for an async launch.
    quoted = 'The transcript said: "Async agent launched successfully. You will be notified automatically."'
    turns = hook_module.build_turns(sync_agent_turn_rows(result_text=quoted))
    state = hook_module.SessionState()

    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert len(turns_to_emit) == 1
    assert state.pending_agent_turns == []


def test_launch_text_fallback_defers_rows_without_tool_use_result(hook_module):
    # Older Claude Code versions have no toolUseResult on the row; the prose
    # heuristic keeps deferral working there.
    rows = launch_turn_rows("toolu_bg")
    rows[2].pop("toolUseResult", None)
    turns = hook_module.build_turns(rows)
    state = hook_module.SessionState()

    turns_to_emit = hook_module.get_turns_to_emit(turns, state)

    assert turns_to_emit == []
    assert len(state.pending_agent_turns) == 1


def make_task_id_notification_row(uuid: str, task_id: str, result: str, timestamp: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": timestamp,
        "uuid": uuid,
        "origin": {"kind": "task-notification"},
        "message": {
            "role": "user",
            "content": (
                f"<task-notification><task-id>{task_id}</task-id>"
                f"<result>{result}</result></task-notification>"
            ),
        },
    }


def write_subagent_meta(transcript: Path, agent_id: str, tool_use_id: str) -> None:
    subagent_dir = transcript.with_suffix("") / "subagents"
    subagent_dir.mkdir(parents=True, exist_ok=True)
    (subagent_dir / f"agent-{agent_id}.meta.json").write_text(
        json.dumps({"toolUseId": tool_use_id, "agentType": "general-purpose", "description": "bg"}),
        encoding="utf-8",
    )
    (subagent_dir / f"agent-{agent_id}.jsonl").write_text("", encoding="utf-8")


def test_unresolvable_notification_is_stashed_and_resolved_when_meta_appears(hook_module, tmp_path):
    """Regression: a task-id-only notification arriving before the subagent's
    meta.json exists (and with no open turn) was consumed and its result
    permanently lost; the deferred turn was flushed without its final output."""
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    def run(flush=False):
        sub_map = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
        turns, new_state = hook_module.get_new_turns_from_transcript(
            transcript, state, sub_map, flush_deferred_agent_turns=flush)
        return hook_module.get_turns_to_emit(turns, new_state, flush_deferred_agent_turns=flush)

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    assert run() == []
    assert state.open_turn_rows
    assert state.pending_agent_turns == []

    # The unresolved notification and later continuation retain their raw-row
    # order while no meta.json exists yet.
    append_jsonl(transcript, [
        make_task_id_notification_row("n1", "agent-late", "Late result.", "2026-01-01T00:01:00.000Z"),
        make_assistant_row(
            "assistant-wait", "msg-wait", [{"type": "text", "text": "Still waiting."}],
            "2026-01-01T00:01:01.000Z",
        ),
    ])
    assert run() == []
    assert len(state.pending_task_notifications) == 1

    # meta.json appears late; the stashed notification resolves on this run.
    write_subagent_meta(transcript, "agent-late", "toolu_bg")
    append_jsonl(transcript, [
        make_user_row("user-2", "Next question.", "2026-01-01T00:02:00.000Z"),
        make_assistant_row("assistant-3", "msg-3",
                           [{"type": "text", "text": "Answer."}], "2026-01-01T00:02:01.000Z"),
    ])
    emitted = run()
    append_jsonl(transcript, [
        make_user_row("user-3", "Final question.", "2026-01-01T00:02:02.000Z"),
    ])
    emitted += run()

    assert [t.user_msg["uuid"] for t in emitted] == ["user-1", "user-2"]
    assert emitted[0].tool_results_by_id["toolu_bg"]["final_content"] == "Late result."
    emitted_user_1_uuids = [row["uuid"] for row in emitted[0].rows if row.get("uuid")]
    assert emitted_user_1_uuids.index("n1") < emitted_user_1_uuids.index("assistant-wait")
    assert state.pending_agent_turns == []
    assert state.pending_task_notifications == []


def test_stashed_notification_resolves_after_boundary_without_duplication(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    notification = make_task_id_notification_row(
        "n1", "agent-late", "Late result.", "2026-01-01T00:01:00.000Z"
    )
    append_jsonl(transcript, [notification])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_task_notifications) == 1

    # The next prompt closes the turn while metadata is still absent. The
    # unresolved row stays only in the notification stash until ownership is known.
    append_jsonl(transcript, [
        make_user_row("user-2", "Next question.", "2026-01-01T00:02:00.000Z"),
        make_assistant_row(
            "assistant-3", "msg-3", [{"type": "text", "text": "Next answer."}],
            "2026-01-01T00:02:01.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_agent_turns) == 1
    assert [row.get("uuid") for row in state.pending_agent_turns[0]["rows"]].count("n1") == 0

    write_subagent_meta(transcript, "agent-late", "toolu_bg")
    sub_map = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, sub_map)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    assert emitted[0].tool_results_by_id["toolu_bg"]["final_content"] == "Late result."
    assert [row.get("uuid") for row in emitted[0].rows].count("n1") == 1
    assert state.pending_agent_turns == []
    assert state.pending_task_notifications == []


def test_unresolved_notification_after_boundary_never_leaks_into_both_turns(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    notification = make_task_id_notification_row(
        "n1", "agent-late", "Late result.", "2026-01-01T00:05:01.000Z"
    )
    append_jsonl(transcript, [
        make_user_row("user-2", "Second question.", "2026-01-01T00:05:00.000Z"),
        notification,
        make_assistant_row(
            "assistant-3", "msg-3", [{"type": "text", "text": "Second answer."}],
            "2026-01-01T00:05:02.000Z",
        ),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_agent_turns) == 1
    assert all(row.get("uuid") != "n1" for row in state.open_turn_rows)

    # Turn 2 can close and emit before ownership metadata appears; it must not
    # contain the unresolved row that ultimately belongs to turn 1.
    append_jsonl(transcript, [
        make_user_row("user-3", "Third question.", "2026-01-01T00:05:03.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted_turn_2 = hook_module.get_turns_to_emit(turns, state)
    assert len(emitted_turn_2) == 1
    assert emitted_turn_2[0].user_msg["uuid"] == "user-2"
    assert all(row.get("uuid") != "n1" for row in emitted_turn_2[0].rows)

    write_subagent_meta(transcript, "agent-late", "toolu_bg")
    sub_map = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, sub_map)
    emitted_turn_1 = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted_turn_1) == 1
    assert emitted_turn_1[0].user_msg["uuid"] == "user-1"
    assert emitted_turn_1[0].tool_results_by_id["toolu_bg"]["final_content"] == "Late result."
    assert [row.get("uuid") for row in emitted_turn_1[0].rows].count("n1") == 1
    turn_1_uuids = {row.get("uuid") for row in emitted_turn_1[0].rows if row.get("uuid")}
    turn_2_uuids = {row.get("uuid") for row in emitted_turn_2[0].rows if row.get("uuid")}
    assert turn_1_uuids.isdisjoint(turn_2_uuids)


def test_multiple_stashed_notifications_keep_order_at_open_turn_tail(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    notifications = [
        make_task_id_notification_row(
            "n1", "agent-late", "First result.", "2026-01-01T00:01:00.000Z"
        ),
        make_task_id_notification_row(
            "n2", "agent-late", "Final result.", "2026-01-01T00:01:01.000Z"
        ),
    ]
    append_jsonl(transcript, notifications)
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_task_notifications) == 2

    write_subagent_meta(transcript, "agent-late", "toolu_bg")
    sub_map = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, sub_map)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_user_row("user-2", "Next question.", "2026-01-01T00:02:00.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state, sub_map)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    notification_uuids = [
        row.get("uuid")
        for row in emitted[0].rows
        if row.get("uuid") in {"n1", "n2"}
    ]
    assert notification_uuids == ["n1", "n2"]
    assert emitted[0].tool_results_by_id["toolu_bg"]["final_content"] == "Final result."


def test_adjacent_unresolved_notification_is_attributed_before_resolvable_duplicate(
    hook_module,
    tmp_path,
):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()

    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []

    append_jsonl(transcript, [
        make_task_id_notification_row(
            "n1", "agent-late", "First result.", "2026-01-01T00:01:00.000Z"
        ),
        make_notification_row(
            "n2",
            "toolu_bg",
            "Final result.",
            "2026-01-01T00:01:01.000Z",
            task_id="agent-late",
        ),
        make_assistant_row(
            "assistant-after", "msg-after", [{"type": "text", "text": "After both."}],
            "2026-01-01T00:01:02.000Z",
        ),
        make_user_row("user-2", "Next question.", "2026-01-01T00:02:00.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    emitted = hook_module.get_turns_to_emit(turns, state)

    assert len(emitted) == 1
    relevant_uuids = [
        row.get("uuid")
        for row in emitted[0].rows
        if row.get("uuid") in {"n1", "n2", "assistant-after"}
    ]
    assert relevant_uuids == ["n1", "n2", "assistant-after"]
    assert emitted[0].tool_results_by_id["toolu_bg"]["final_content"] == "Final result."
    assert state.pending_task_notifications == []


def test_stashed_notification_without_matching_deferred_turn_is_dropped(hook_module):
    state = hook_module.SessionState(
        pending_task_notifications=[
            make_notification_row("n1", "toolu_gone", "Result", "2026-01-01T00:01:00.000Z"),
        ],
    )

    resolved, remaining = hook_module.resolve_deferred_agent_turns([], state)

    # Resolvable but nothing waits for it (turn already emitted): drop it
    # instead of re-stashing forever.
    assert resolved == []
    assert remaining == []
    assert state.pending_task_notifications == []


def test_unresolved_notifications_are_dropped_at_session_end_flush(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    state = hook_module.SessionState()
    append_jsonl(transcript, launch_turn_rows("toolu_bg"))
    append_jsonl(transcript, [
        make_task_id_notification_row("n1", "agent-unknown", "Result.", "2026-01-01T00:01:00.000Z"),
    ])
    turns, state = hook_module.get_new_turns_from_transcript(transcript, state)
    assert hook_module.get_turns_to_emit(turns, state) == []
    assert len(state.pending_task_notifications) == 1

    flushed_turns, state = hook_module.get_new_turns_from_transcript(
        transcript, state, flush_deferred_agent_turns=True)
    flushed = hook_module.get_turns_to_emit(flushed_turns, state, flush_deferred_agent_turns=True)

    # The deferred turn is still flushed (without its final output) and the
    # unresolvable notification does not linger in the state file.
    assert len(flushed) == 1
    assert state.pending_task_notifications == []
    assert state.pending_agent_turns == []


def test_stashed_notifications_are_bounded(hook_module):
    state = hook_module.SessionState()
    rows = [
        make_task_id_notification_row(f"n{i}", f"agent-{i}", "r", "2026-01-01T00:01:00.000Z")
        for i in range(hook_module.MAX_PENDING_TASK_NOTIFICATIONS + 10)
    ]

    hook_module.resolve_deferred_agent_turns(rows, state)

    assert len(state.pending_task_notifications) == hook_module.MAX_PENDING_TASK_NOTIFICATIONS
    # the newest notifications win
    assert state.pending_task_notifications[-1]["uuid"] == rows[-1]["uuid"]


def test_session_state_round_trips_stashed_notifications(hook_module):
    notification = make_notification_row("n1", "toolu_bg", "Result", "2026-01-01T00:01:00.000Z")
    context = {"insert_after_uuid": "assistant-1", "insert_before_uuid": "assistant-2"}
    state = hook_module.SessionState(
        pending_task_notifications=[notification],
        pending_task_notification_contexts=[context],
    )
    global_state: dict[str, Any] = {}

    hook_module.update_session_state(global_state, "key", state)
    restored = hook_module.get_session_state(global_state, "key")

    assert restored.pending_task_notifications == [notification]
    assert restored.pending_task_notification_contexts == [context]
