from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest


def expected_trace_id(seed: str, turn_number: int) -> int:
    """The formula external systems use: Langfuse.create_trace_id(seed=f"{seed}:{turn}")."""
    return int(hashlib.sha256(f"{seed}:{turn_number}".encode("utf-8")).hexdigest()[:32], 16)


def write_two_turn_transcript(tmp_path: Path) -> Path:
    rows = [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:00.000Z",
            "sessionId": "session-seeded",
            "uuid": "user-1",
            "message": {"role": "user", "content": "First question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:01.000Z",
            "sessionId": "session-seeded",
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
            "sessionId": "session-seeded",
            "uuid": "user-2",
            "message": {"role": "user", "content": "Second question."},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:03.000Z",
            "sessionId": "session-seeded",
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


def get_forced_trace_id(observation: Any) -> int | None:
    """Trace id forced onto a root span via its remote parent context, if any."""
    context = observation._otel_span.context
    if context is None:
        return None
    return context["current_span"].get_span_context().trace_id


def test_seeded_turns_get_individually_predictable_trace_ids(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)
    seed = "5b6e9821-3f0a-4b8f-9d0e-000000000001"
    config = hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed=seed
    )

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-seeded", transcript
    )

    assert emitted == 2
    roots = get_root_observations(fake_langfuse)
    assert len(roots) == 2
    first_id, second_id = (get_forced_trace_id(root) for root in roots)
    assert first_id == expected_trace_id(seed, 1)
    assert second_id == expected_trace_id(seed, 2)
    assert first_id != second_id

    # The remote parent context carries a sampled, remote span context so
    # the SDK exports the span under the derived trace id.
    parent_context = roots[0]._otel_span.context["current_span"].get_span_context()
    assert parent_context.is_remote is True
    assert parent_context.span_id != 0
    assert int(parent_context.trace_flags) & 0x01

    # Only the root span is started in the forced context; children inherit
    # the trace via their parent span as before.
    for observation in fake_langfuse.observations:
        if observation.name != "Conversational Turn":
            assert observation._otel_span.context is None


def test_turn_numbers_continue_across_hook_runs(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)
    seed = "session-abc"
    config = hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed=seed
    )

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, "session-seeded", transcript)

    # Append a third turn and run the hook again: it must derive from the
    # persisted turn_count, not restart at 1.
    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "user",
            "timestamp": "2026-01-01T00:00:04.000Z",
            "sessionId": "session-seeded",
            "uuid": "user-3",
            "message": {"role": "user", "content": "Third question."},
        }) + "\n")
        f.write(json.dumps({
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:05.000Z",
            "sessionId": "session-seeded",
            "uuid": "assistant-3",
            "message": {
                "id": "msg-3",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "Third answer."}],
            },
        }) + "\n")

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-seeded", transcript
    )

    assert emitted == 1
    assert get_forced_trace_id(get_root_observations(fake_langfuse)[-1]) == expected_trace_id(seed, 3)


def test_without_seed_trace_ids_stay_auto_generated(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = write_two_turn_transcript(tmp_path)
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-seeded", transcript
    )

    assert emitted == 2
    for root in get_root_observations(fake_langfuse):
        assert root._otel_span.context is None


def test_derivation_failure_falls_back_to_auto_generated_trace_id(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    transcript = write_two_turn_transcript(tmp_path)
    config = hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed="session-abc"
    )

    def _boom(trace_seed: str, turn_number: int) -> str:
        raise RuntimeError("derivation broke")

    monkeypatch.setattr(hook_module, "derive_turn_trace_id", _boom)

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-seeded", transcript
    )

    assert emitted == 2
    roots = get_root_observations(fake_langfuse)
    assert len(roots) == 2
    for root in roots:
        assert root._otel_span.context is None


def test_forced_context_failure_falls_back_to_auto_generated_trace_id(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, monkeypatch: pytest.MonkeyPatch
):
    transcript = write_two_turn_transcript(tmp_path)
    config = hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed="session-abc"
    )

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("otel context broke")

    monkeypatch.setattr(hook_module.otel_trace_api, "SpanContext", _boom)

    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, "session-seeded", transcript
    )

    assert emitted == 2
    for root in get_root_observations(fake_langfuse):
        assert root._otel_span.context is None
