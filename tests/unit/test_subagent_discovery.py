from __future__ import annotations

import json


def test_discovers_subagent_transcripts_by_parent_tool_use_id(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("async_agent_completed")

    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    assert set(subagents) == {"toolu_agent_complete"}
    subagent = subagents["toolu_agent_complete"]
    assert subagent["agent_id"] == "agent-complete"
    assert subagent["agent_type"] == "general-purpose"
    assert subagent["description"] == "Summarize docs"
    assert subagent["path"].name == "agent-agent-complete.jsonl"


def test_discovers_nested_subagent_metadata_seen_in_real_claude_code_transcripts(
    hook_module,
    fixture_transcript_path,
):
    transcript = fixture_transcript_path("nested_subagents")

    subagents = hook_module.get_subagent_transcripts_by_tool_use_id(transcript)

    assert set(subagents) == {"toolu_outer_agent", "toolu_inner_agent"}
    assert subagents["toolu_outer_agent"]["agent_id"] == "outer-agent"
    assert subagents["toolu_inner_agent"]["agent_id"] == "inner-agent"
    assert subagents["toolu_inner_agent"]["agent_type"] == "fork"


def test_ignores_bad_or_incomplete_subagent_metadata(hook_module, tmp_path):
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("", encoding="utf-8")
    subagent_dir = tmp_path / "transcript" / "subagents"
    subagent_dir.mkdir(parents=True)
    (subagent_dir / "agent-bad.meta.json").write_text("{not-json", encoding="utf-8")
    (subagent_dir / "agent-missing-jsonl.meta.json").write_text(
        json.dumps({"toolUseId": "toolu_missing"}),
        encoding="utf-8",
    )
    (subagent_dir / "agent-missing-tool-id.meta.json").write_text(
        json.dumps({"description": "No tool id"}),
        encoding="utf-8",
    )
    (subagent_dir / "agent-missing-tool-id.jsonl").write_text("", encoding="utf-8")

    assert hook_module.get_subagent_transcripts_by_tool_use_id(transcript) == {}
