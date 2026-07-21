from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest


PARENT_TRACE_ID = "1234567890abcdef1234567890abcdef"
PARENT_SPAN_ID = "fedcba0987654321"


def attached_config(hook_module: Any, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = dict(
        parent_trace_id=PARENT_TRACE_ID,
        parent_span_id=PARENT_SPAN_ID,
    )
    kwargs.update(overrides)
    return hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", **kwargs
    )


def write_two_turn_transcript(tmp_path: Path) -> Path:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": "session-attached",
            "uuid": "user-1",
            "message": {"role": "user", "content": "First question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": "session-attached",
            "uuid": "assistant-1",
            "message": {
                "id": "msg-1",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "First answer."}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:02.000Z",
            "sessionId": "session-attached",
            "uuid": "user-2",
            "message": {"role": "user", "content": "Second question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:03.000Z",
            "sessionId": "session-attached",
            "uuid": "assistant-2",
            "message": {
                "id": "msg-2",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "Second answer."}],
            },
        },
    ]
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return transcript


def get_root_observations(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def get_remote_parent_span_context(observation: Any) -> Any | None:
    context = observation._otel_span.context
    if context is None:
        return None
    return context["current_span"].get_span_context()


def test_attached_turns_nest_under_the_external_parent_span(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)
    config = attached_config(hook_module)

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-attached", transcript
    )

    # The trailing turn stays open but its root is already opened (async
    # activity resolved), so both roots exist.
    assert emitted == 1
    roots = get_root_observations(fake_langfuse)
    assert len(roots) == 2
    for root in roots:
        parent = get_remote_parent_span_context(root)
        assert parent is not None
        assert parent.trace_id == int(PARENT_TRACE_ID, 16)
        assert parent.span_id == int(PARENT_SPAN_ID, 16)
        assert parent.is_remote is True
        assert int(parent.trace_flags) & 0x01


def test_attached_roots_do_not_claim_the_trace_root(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, attached_config(hook_module), "session-attached", transcript
    )

    for root in get_root_observations(fake_langfuse):
        attributes = root._otel_span.attributes
        # The launching application owns the trace root.
        assert "langfuse.internal.as_root" not in attributes
        assert attributes.get("langfuse.internal.is_app_root") is False


def test_attached_mode_does_not_propagate_trace_level_attributes(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    transcript = write_two_turn_transcript(tmp_path)
    calls: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def recording_propagate(**kwargs: Any):
        calls.append(kwargs)
        yield

    monkeypatch.setattr(hook_module, "propagate_attributes", recording_propagate)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, attached_config(hook_module), "session-attached", transcript
    )
    assert calls == []

    # Standalone mode keeps propagating trace name, session, user and tags.
    standalone_dir = tmp_path / "standalone"
    standalone_dir.mkdir()
    standalone = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, standalone, "session-standalone", write_two_turn_transcript(standalone_dir)
    )
    assert calls
    assert all("trace_name" in call for call in calls)


def test_attached_open_turn_resumes_without_reopening_its_root(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)
    config = attached_config(hook_module)

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-attached", transcript)
    roots_after_first_firing = len(get_root_observations(fake_langfuse))

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-attached", transcript)

    assert len(get_root_observations(fake_langfuse)) == roots_after_first_firing


def test_carrier_failure_falls_back_to_standalone_emission(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    transcript = write_two_turn_transcript(tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("otel context broke")

    monkeypatch.setattr(hook_module.otel_trace_api, "SpanContext", _boom)

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, attached_config(hook_module), "session-attached", transcript
    )

    # Turns still ship; they just cannot join the external trace.
    assert emitted == 1
    roots = get_root_observations(fake_langfuse)
    assert len(roots) == 2
    for root in roots:
        assert root._otel_span.context is None


def test_without_parent_context_behavior_is_unchanged(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-attached", transcript
    )

    assert emitted == 1
    for root in get_root_observations(fake_langfuse):
        assert root._otel_span.context is None
        assert root._otel_span.attributes.get("langfuse.internal.as_root") is True
