from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

ORIGINAL_SESSION = "aaaaaaaa-1111-1111-1111-111111111111"
FORK_SESSION = "bbbbbbbb-2222-2222-2222-222222222222"


def turn_rows(session_id: str, index: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "user",
            "timestamp": f"2026-08-11T14:4{index}:00.000Z",
            "sessionId": session_id,
            "uuid": f"user-row-{index}",
            "message": {"role": "user", "content": f"Question {index}?"},
        },
        {
            "type": "assistant",
            "timestamp": f"2026-08-11T14:4{index}:05.000Z",
            "sessionId": session_id,
            "uuid": f"assistant-row-{index}",
            "message": {
                "id": f"msg-{index}",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": f"Answer {index}."}],
                "usage": {"input_tokens": 2, "output_tokens": 540},
            },
        },
    ]


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def write_original_transcript(tmp_path: Path, turns: int = 3) -> Path:
    """A transcript named after its session, holding `turns` complete turns."""
    transcript = tmp_path / f"{ORIGINAL_SESSION}.jsonl"
    rows: list[dict[str, Any]] = []
    for index in range(1, turns + 1):
        rows.extend(turn_rows(ORIGINAL_SESSION, index))
    write_rows(transcript, rows)
    return transcript


def fork_transcript(original: Path, tmp_path: Path) -> Path:
    """Copy the history verbatim into a file named after the new session id.

    Mirrors what --fork-session writes: the copied rows keep the original
    session id, only the file name carries the new one.
    """
    fork = tmp_path / f"{FORK_SESSION}.jsonl"
    shutil.copyfile(original, fork)
    return fork


def config_for(hook_module: Any, trace_seed: str | None = None) -> Any:
    return hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", "user-1", trace_seed=trace_seed
    )


def roots(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.name == "Conversational Turn"]


def generations(fake_langfuse: Any) -> list[Any]:
    return [o for o in fake_langfuse.observations if o.as_type == "generation"]


def turn_numbers(fake_langfuse: Any) -> list[int]:
    return [(o.kwargs.get("metadata") or {}).get("turn_number") for o in roots(fake_langfuse)]


def test_forked_session_does_not_re_export_the_copied_history(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    original = write_original_transcript(tmp_path)
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, ORIGINAL_SESSION, original
    )
    after_original = len(fake_langfuse.observations)
    assert len(generations(fake_langfuse)) == 3

    # The fork copies the history and the user asks one more question in it.
    fork = fork_transcript(original, tmp_path)
    append_rows(fork, turn_rows(FORK_SESSION, 4))
    append_rows(fork, turn_rows(FORK_SESSION, 5))

    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, FORK_SESSION, fork)

    # Only the two turns the fork itself produced are exported; the three
    # copied ones were already exported by the session they came from.
    fork_observations = fake_langfuse.observations[after_original:]
    fork_roots = [o for o in fork_observations if o.name == "Conversational Turn"]
    assert len(fork_roots) == 2
    assert len(generations(fake_langfuse)) == 5


def test_forked_turns_continue_the_turn_numbering_of_the_copied_history(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    original = write_original_transcript(tmp_path)
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, ORIGINAL_SESSION, original
    )
    assert turn_numbers(fake_langfuse) == [1, 2, 3]

    fork = fork_transcript(original, tmp_path)
    append_rows(fork, turn_rows(FORK_SESSION, 4))
    append_rows(fork, turn_rows(FORK_SESSION, 5))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, FORK_SESSION, fork)

    # Restarting at 1 would collide with the original turns under
    # CC_LANGFUSE_TRACE_SEED, whose trace ids derive from the turn number.
    assert turn_numbers(fake_langfuse) == [1, 2, 3, 4, 5]


def test_seeded_fork_turns_get_trace_ids_of_their_own(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    seed = "deployment-seed"
    original = write_original_transcript(tmp_path)
    config = config_for(hook_module, trace_seed=seed)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, ORIGINAL_SESSION, original
    )
    original_trace_ids = {
        o._otel_span.context["current_span"].get_span_context().trace_id
        for o in roots(fake_langfuse)
        if o._otel_span.context
    }

    fork = fork_transcript(original, tmp_path)
    append_rows(fork, turn_rows(FORK_SESSION, 4))
    append_rows(fork, turn_rows(FORK_SESSION, 5))
    before = len(fake_langfuse.observations)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, FORK_SESSION, fork)

    fork_trace_ids = {
        o._otel_span.context["current_span"].get_span_context().trace_id
        for o in fake_langfuse.observations[before:]
        if o.name == "Conversational Turn" and o._otel_span.context
    }
    assert fork_trace_ids
    assert not (fork_trace_ids & original_trace_ids)
    expected = int(hashlib.sha256(f"{seed}:4".encode("utf-8")).hexdigest()[:32], 16)
    assert expected in fork_trace_ids


def test_plain_session_keeps_every_row(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # A session whose rows carry its own id must be untouched by the check.
    original = write_original_transcript(tmp_path)
    config = config_for(hook_module)

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, ORIGINAL_SESSION, original
    )

    assert len(generations(fake_langfuse)) == 3
    assert turn_numbers(fake_langfuse) == [1, 2, 3]


def test_rows_without_a_session_id_are_kept(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    transcript = tmp_path / f"{ORIGINAL_SESSION}.jsonl"
    rows = turn_rows(ORIGINAL_SESSION, 1)
    for row in rows:
        del row["sessionId"]
    write_rows(transcript, rows)
    write_rows(transcript, rows + turn_rows(ORIGINAL_SESSION, 2))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), ORIGINAL_SESSION, transcript
    )

    assert len(generations(fake_langfuse)) == 2


def test_transcript_not_named_after_the_session_is_left_alone(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # If the payload id and the file name ever stop agreeing, exporting
    # duplicates is better than silently exporting nothing at all.
    transcript = tmp_path / "some-other-name.jsonl"
    write_rows(transcript, turn_rows(ORIGINAL_SESSION, 1) + turn_rows(ORIGINAL_SESSION, 2))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), "a-different-session-id", transcript
    )

    assert len(generations(fake_langfuse)) == 2


def test_fork_without_any_new_activity_exports_nothing(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    original = write_original_transcript(tmp_path)
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config, ORIGINAL_SESSION, original
    )
    before = len(fake_langfuse.observations)

    # A fork that was opened but never continued holds copied history only.
    fork = fork_transcript(original, tmp_path)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, FORK_SESSION, fork)

    assert len(fake_langfuse.observations) == before


def test_fork_turns_see_the_copied_history_in_their_generation_input(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    # The copied rows are never exported a second time, but the model did
    # read them, so they must still appear in the input of the fork's turns.
    original = write_original_transcript(tmp_path)
    fork = fork_transcript(original, tmp_path)
    append_rows(fork, turn_rows(FORK_SESSION, 4))
    append_rows(fork, turn_rows(FORK_SESSION, 5))

    hook_module.emit_new_turns_from_transcript(
        fake_langfuse, config_for(hook_module), FORK_SESSION, fork
    )

    inputs = [generation.kwargs["input"] for generation in generations(fake_langfuse)]
    assert len(inputs) == 2
    # The fork's first turn opens with the copied question, not its own.
    assert inputs[0][0] == {"role": "user", "content": "Question 1?"}
    assert inputs[0][-1] == {"role": "user", "content": "Question 4?"}
    assert len(inputs[0]) == 7
    # The next turn adds its predecessor, so the history keeps accumulating.
    assert inputs[1][-1] == {"role": "user", "content": "Question 5?"}
    assert len(inputs[1]) == 9
