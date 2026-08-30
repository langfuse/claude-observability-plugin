"""CC_LANGFUSE_TAGS adds caller-chosen tags to every trace."""
from __future__ import annotations

import pytest


def _empty_turn(hook_module):
    return hook_module.Turn(
        user_msg={},
        assistant_msgs=[],
        tool_results_by_id={},
        tool_use_timestamps_by_id={},
        injected_by_tool_id={},
        rows=[],
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", []),
        ("ticket-123", ["ticket-123"]),
        (" team:platform , ticket-123 ,, ", ["team:platform", "ticket-123"]),
        ("a,b,a", ["a", "b"]),
    ],
)
def test_parse_tags_drops_blanks_and_duplicates(hook_module, raw, expected):
    assert hook_module._parse_tags(raw) == expected


def test_extra_tags_follow_claude_code_and_never_duplicate_it(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "EXTRA_TAGS", ["claude-code", "ticket-123"])
    monkeypatch.setattr(hook_module, "SKILL_TAGS", False)
    assert hook_module.get_trace_tags(_empty_turn(hook_module)) == ["claude-code", "ticket-123"]


def test_no_extra_tags_leaves_default(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "EXTRA_TAGS", [])
    monkeypatch.setattr(hook_module, "SKILL_TAGS", False)
    assert hook_module.get_trace_tags(_empty_turn(hook_module)) == ["claude-code"]
