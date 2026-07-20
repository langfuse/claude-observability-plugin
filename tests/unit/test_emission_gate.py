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
