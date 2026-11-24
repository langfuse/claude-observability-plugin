from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

SESSION = "cccccccc-3333-3333-3333-333333333333"


def turn_rows(index: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "user",
            "timestamp": f"2026-11-04T10:0{index}:00.000Z",
            "sessionId": SESSION,
            "uuid": f"user-row-{index}",
            "message": {"role": "user", "content": f"Question {index}?"},
        },
        {
            "type": "assistant",
            "timestamp": f"2026-11-04T10:0{index}:05.000Z",
            "sessionId": SESSION,
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


def append_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def append_turns(path: Path, indices: range) -> None:
    for index in indices:
        append_rows(path, turn_rows(index))


def transcript_in(tmp_path: Path, slug: str) -> Path:
    directory = tmp_path / "projects" / slug
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{SESSION}.jsonl"


def move(transcript: Path, destination: Path, keep_original: bool) -> Path:
    """Claude Code CLI renames the file on EnterWorktree; a copy leaves both."""
    if keep_original:
        shutil.copyfile(transcript, destination)
    else:
        shutil.move(transcript, destination)
    return destination


def config_for(hook_module: Any) -> Any:
    return hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")


def turn_numbers(observations: list[Any]) -> list[int]:
    return [
        (o.kwargs.get("metadata") or {}).get("turn_number")
        for o in observations
        if o.name == "Conversational Turn"
    ]


@pytest.mark.parametrize("keep_original", [False, True], ids=["renamed", "copied"])
def test_moved_session_exports_only_turns_written_after_the_move(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path, keep_original
):
    original = transcript_in(tmp_path, "C--repo")
    append_turns(original, range(1, 4))
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, original)
    assert turn_numbers(fake_langfuse.observations) == [1, 2, 3]
    before_move = len(fake_langfuse.observations)

    moved = move(original, transcript_in(tmp_path, "C--repo--claude-worktrees-live"), keep_original)
    append_turns(moved, range(4, 6))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, moved)

    assert turn_numbers(fake_langfuse.observations[before_move:]) == [4, 5]


def test_session_moved_back_exports_only_turns_written_after_the_return(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    original = transcript_in(tmp_path, "C--repo")
    append_turns(original, range(1, 3))
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, original)

    worktree = move(original, transcript_in(tmp_path, "C--repo--claude-worktrees-live"), False)
    append_turns(worktree, range(3, 5))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, worktree)
    before_return = len(fake_langfuse.observations)

    back = move(worktree, original, False)
    append_turns(back, range(5, 7))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, back)

    assert turn_numbers(fake_langfuse.observations[before_return:]) == [5, 6]


def test_turn_open_at_the_move_is_exported_once(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    original = transcript_in(tmp_path, "C--repo")
    append_turns(original, range(1, 3))
    question, answer = turn_rows(3)
    append_rows(original, [question])
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, original)

    moved = move(original, transcript_in(tmp_path, "C--repo--claude-worktrees-live"), False)
    append_rows(moved, [answer])
    append_turns(moved, range(4, 5))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, moved)

    assert sorted(turn_numbers(fake_langfuse.observations)) == [1, 2, 3, 4]
    generations = [o for o in fake_langfuse.observations if o.as_type == "generation"]
    assert len(generations) == 4


def test_process_still_writing_the_old_path_after_the_move_does_not_take_the_session(
    hook_module, fake_langfuse, isolated_hook_state, tmp_path
):
    original = transcript_in(tmp_path, "C--repo")
    append_turns(original, range(1, 3))
    config = config_for(hook_module)
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, original)

    worktree = move(original, transcript_in(tmp_path, "C--repo--claude-worktrees-live"), False)
    append_turns(worktree, range(3, 4))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, worktree)

    # A second Claude Code process on the same session keeps writing the old
    # path, which starts a new, shorter file there.
    append_turns(original, range(9, 10))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, original)
    before_next_turn = len(fake_langfuse.observations)

    append_turns(worktree, range(4, 5))
    hook_module.emit_new_turns_from_transcript(fake_langfuse, config, SESSION, worktree)

    assert turn_numbers(fake_langfuse.observations[before_next_turn:]) == [4]
