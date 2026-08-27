from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _no_ambient_custom_tags(hook_module, monkeypatch):
    """hook_module is session-scoped, so CUSTOM_TAGS froze at import from the
    developer's own environment. Reset it per test; cases that want custom tags
    set them explicitly with monkeypatch.setattr (setenv would have no effect)."""
    monkeypatch.setattr(hook_module, "CUSTOM_TAGS", [])


def make_user_row(text: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-01-01T00:00:00.000Z",
        "uuid": "user-1",
        "message": {"role": "user", "content": text},
    }


def make_skill_tool_use_row(skill: str) -> dict[str, Any]:
    """Invocation path 1: Claude calls the Skill tool itself."""
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:01.000Z",
        "uuid": "assistant-tool",
        "message": {
            "id": "msg-tool",
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_skill",
                    "name": "Skill",
                    "input": {"skill": skill, "args": "do the thing"},
                }
            ],
        },
    }


def make_attributed_assistant_row(skill: str, uuid: str = "assistant-attr") -> dict[str, Any]:
    """Invocation path 2: slash command — the harness expands the skill and
    marks the assistant rows with a top-level attributionSkill field."""
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:02.000Z",
        "uuid": uuid,
        "attributionSkill": skill,
        "message": {
            "id": f"msg-{uuid}",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": "Greetings, esteemed colleague."}],
        },
    }


def collect_tags(hook_module, rows: list[dict[str, Any]]) -> list[str]:
    turns = hook_module.build_turns(rows)
    assert len(turns) == 1
    return hook_module.collect_skill_tags(turns[0])


def test_skill_invoked_via_tool_call_is_tagged(hook_module):
    rows = [
        make_user_row("What are the current model ids?"),
        make_skill_tool_use_row("claude-api"),
    ]

    assert collect_tags(hook_module, rows) == ["skill:claude-api"]


def test_skill_invoked_via_slash_command_is_tagged(hook_module):
    # Slash commands never produce a Skill tool_use block; the skill shows up
    # only as attributionSkill on the assistant rows (GitHub #15).
    rows = [
        make_user_row("<command-message>greeting-style</command-message>\n<command-name>/greeting-style</command-name>"),
        make_attributed_assistant_row("greeting-style"),
    ]

    assert collect_tags(hook_module, rows) == ["skill:greeting-style"]


def test_skills_from_both_invocation_paths_are_tagged(hook_module):
    rows = [
        make_user_row("Do two things."),
        make_skill_tool_use_row("claude-api"),
        make_attributed_assistant_row("greeting-style"),
    ]

    assert sorted(collect_tags(hook_module, rows)) == [
        "skill:claude-api",
        "skill:greeting-style",
    ]


def test_same_skill_from_both_paths_is_tagged_once(hook_module):
    rows = [
        make_user_row("Do one thing."),
        make_skill_tool_use_row("greeting-style"),
        make_attributed_assistant_row("greeting-style"),
    ]

    assert collect_tags(hook_module, rows) == ["skill:greeting-style"]


def make_agent_launch_row(tool_use_id: str) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:01.000Z",
        "uuid": "assistant-agent",
        "message": {
            "id": "msg-agent",
            "role": "assistant",
            "model": "claude-test",
            "content": [
                {"type": "tool_use", "id": tool_use_id, "name": "Agent",
                 "input": {"description": "Research", "prompt": "Go research"}}
            ],
        },
    }


def write_subagent_transcript(tmp_path, agent_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    import json as _json
    path = tmp_path / f"agent-{agent_id}.jsonl"
    path.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return {"path": path, "agent_id": agent_id, "agent_type": "general-purpose", "description": "bg"}


def test_skills_used_inside_subagents_are_tagged_with_own_prefix(hook_module, tmp_path):
    subagent_rows = [
        make_user_row("Subagent prompt."),
        make_attributed_assistant_row("deep-research", uuid="sub-assistant-1"),
        make_skill_tool_use_row("claude-api"),
    ]
    sub_map = {"toolu_agent_1": write_subagent_transcript(tmp_path, "a1", subagent_rows)}
    turns = hook_module.build_turns([
        make_user_row("Run an agent."),
        make_agent_launch_row("toolu_agent_1"),
    ])

    tags = hook_module.collect_subagent_skill_tags(turns[0], sub_map)

    assert sorted(tags) == ["subagent-skill:claude-api", "subagent-skill:deep-research"]


def test_subagent_skills_do_not_leak_into_main_skill_namespace(hook_module, tmp_path):
    sub_map = {"toolu_agent_1": write_subagent_transcript(tmp_path, "a1", [
        make_attributed_assistant_row("deep-research", uuid="sub-assistant-1"),
    ])}
    turns = hook_module.build_turns([
        make_user_row("Run an agent."),
        make_agent_launch_row("toolu_agent_1"),
    ])

    assert hook_module.collect_skill_tags(turns[0]) == []
    assert hook_module.get_trace_tags(turns[0], sub_map) == [
        "claude-code",
        "subagent-skill:deep-research",
    ]


def test_same_skill_across_two_subagents_is_tagged_once(hook_module, tmp_path):
    row = make_attributed_assistant_row("deep-research", uuid="sub-assistant-1")
    sub_map = {
        "toolu_agent_1": write_subagent_transcript(tmp_path, "a1", [row]),
        "toolu_agent_2": write_subagent_transcript(tmp_path, "a2", [row]),
    }
    launch = make_agent_launch_row("toolu_agent_1")
    launch2 = make_agent_launch_row("toolu_agent_2")
    launch2["uuid"] = "assistant-agent-2"
    launch2["message"] = dict(launch2["message"], id="msg-agent-2")
    turns = hook_module.build_turns([make_user_row("Run two agents."), launch, launch2])

    assert hook_module.collect_subagent_skill_tags(turns[0], sub_map) == [
        "subagent-skill:deep-research",
    ]


# --- Operator tags from CC_LANGFUSE_TAGS -------------------------------------
# hook_module is session-scoped, so CUSTOM_TAGS cannot be driven with setenv;
# these set the parsed value directly, which is what the hook reads.


def make_plain_assistant_row() -> dict[str, Any]:
    """An assistant turn that invokes no skill, so only custom tags can appear."""
    return {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:03.000Z",
        "uuid": "assistant-plain",
        "message": {
            "id": "msg-plain",
            "role": "assistant",
            "model": "claude-test",
            "content": [{"type": "text", "text": "No skill was used here."}],
        },
    }


def trace_tags(hook_module, rows: list[dict[str, Any]]) -> list[str]:
    turns = hook_module.build_turns(rows)
    assert len(turns) == 1
    return hook_module.get_trace_tags(turns[0])


def test_custom_tags_follow_the_base_tag(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAGS", ["env:prod"])

    assert trace_tags(hook_module, [
        make_user_row("Hello."),
        make_plain_assistant_row(),
    ]) == ["claude-code", "env:prod"]


def test_custom_tags_precede_skill_tags(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAGS", ["env:prod"])

    assert trace_tags(hook_module, [
        make_user_row("Use a skill."),
        make_skill_tool_use_row("claude-api"),
    ]) == ["claude-code", "env:prod", "skill:claude-api"]


def test_no_custom_tags_leaves_trace_tags_unchanged(hook_module):
    assert trace_tags(hook_module, [
        make_user_row("Hello."),
        make_plain_assistant_row(),
    ]) == ["claude-code"]


def test_custom_tags_apply_when_skill_tags_are_disabled(hook_module, monkeypatch):
    """CC_LANGFUSE_SKILL_TAGS=false must not discard operator-supplied labels."""
    monkeypatch.setattr(hook_module, "CUSTOM_TAGS", ["env:prod"])
    monkeypatch.setattr(hook_module, "SKILL_TAGS", False)

    assert trace_tags(hook_module, [
        make_user_row("Use a skill."),
        make_skill_tool_use_row("claude-api"),
    ]) == ["claude-code", "env:prod"]


def test_custom_tag_duplicating_a_skill_tag_appears_once(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAGS", ["skill:claude-api"])

    assert trace_tags(hook_module, [
        make_user_row("Use a skill."),
        make_skill_tool_use_row("claude-api"),
    ]) == ["claude-code", "skill:claude-api"]


def test_custom_tag_duplicating_the_base_tag_appears_once(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAGS", ["claude-code"])

    assert trace_tags(hook_module, [
        make_user_row("Hello."),
        make_plain_assistant_row(),
    ]) == ["claude-code"]
