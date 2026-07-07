from __future__ import annotations


def test_subagent_observations_skip_malformed_jsonl_lines(
    hook_module,
    fake_langfuse,
    fixture_transcript_path,
    tmp_path,
):
    transcript = fixture_transcript_path("async_agent_completed")
    source_path = transcript.with_suffix("") / "subagents" / "agent-agent-complete.jsonl"
    source_lines = source_path.read_text(encoding="utf-8").splitlines()
    malformed_path = tmp_path / "agent-bad-line.jsonl"
    malformed_path.write_text(
        "\n".join([source_lines[0], "{bad-json", *source_lines[1:]]) + "\n",
        encoding="utf-8",
    )

    end_timestamp = hook_module.emit_subagent_observations(
        fake_langfuse,
        None,
        {
            "path": malformed_path,
            "description": "Summarize docs",
            "agent_type": "general-purpose",
        },
        None,
    )

    assert end_timestamp.isoformat() == "2026-01-01T00:02:04.500000+00:00"
    assert [observation.name for observation in fake_langfuse.observations] == [
        "Subagent: Summarize docs",
        "Subagent LLM Call 1",
        "Tool: ToolSearch",
        "Subagent LLM Call 2",
    ]
