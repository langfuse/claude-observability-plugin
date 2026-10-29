from __future__ import annotations

NOTIFICATION = (
    "<task-notification>\n<task-id>t1</task-id>\n"
    "<tool-use-id>toolu_X</tool-use-id>\n<result>done</result>\n</task-notification>"
)


def test_queued_notification_counts_as_undelivered(hook_module):
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTIFICATION},
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == ["toolu_X"]


def test_delivery_clears_the_queued_notification(hook_module):
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTIFICATION},
        {"type": "user", "origin": {"kind": "task-notification"},
         "message": {"role": "user", "content": NOTIFICATION}},
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == []


def test_rows_without_notifications_pass_the_gate(hook_module):
    rows = [
        {"type": "user", "message": {"role": "user", "content": "hallo"}},
        {"type": "queue-operation", "operation": "remove", "content": None},
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == []


def test_attachment_delivery_clears_the_queued_notification(hook_module):
    # Claude Code 2.1.281 attaches a notification that lands mid-turn to the
    # running turn (queued_command attachment) instead of writing a user row.
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTIFICATION},
        {
            "type": "attachment",
            "attachment": {
                "type": "queued_command",
                "commandMode": "task-notification",
                "prompt": NOTIFICATION,
            },
        },
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == []


def test_absorbed_mid_turn_notification_clears_the_gate(hook_module):
    # The remove row repeats the notification content; the attachment already
    # proved delivery, so the turn may ship at its own Stop.
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTIFICATION},
        {
            "type": "attachment",
            "attachment": {
                "type": "queued_command",
                "commandMode": "task-notification",
                "prompt": NOTIFICATION,
            },
        },
        {
            "type": "queue-operation",
            "operation": "remove",
            "reason": "absorbed_mid_turn",
            "content": NOTIFICATION,
        },
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == []


def test_content_bearing_remove_alone_clears_the_gate(hook_module):
    # Older Claude Code versions omit the attachment row but still write the
    # remove row with the notification content.
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTIFICATION},
        {
            "type": "queue-operation",
            "operation": "remove",
            "reason": "absorbed_mid_turn",
            "content": NOTIFICATION,
        },
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == []


def test_removal_without_content_keeps_the_gate_closed(hook_module):
    # A remove row that cannot be matched must stay conservative.
    rows = [
        {"type": "queue-operation", "operation": "enqueue", "content": NOTIFICATION},
        {"type": "queue-operation", "operation": "remove", "content": None},
    ]
    assert hook_module.get_undelivered_queued_notification_ids(rows) == ["toolu_X"]
