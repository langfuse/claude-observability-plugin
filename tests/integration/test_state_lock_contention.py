from __future__ import annotations

import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_emit(hook_module: Any, fake_langfuse: Any, session_id: str, transcript: Path) -> int:
    config = hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")
    return hook_module.emit_new_turns_from_transcript(
        fake_langfuse,
        config,
        session_id,
        transcript,
        flush_deferred_agent_turns=True,
    )


def _during_emit(hook_module: Any, monkeypatch: pytest.MonkeyPatch, callback: Any) -> None:
    """Run callback in the middle of the emit, where the old code held the lock."""
    original = hook_module.emit_and_close_ready_turns

    def wrapped(*args: Any, **kwargs: Any) -> int:
        callback()
        return original(*args, **kwargs)

    monkeypatch.setattr(hook_module, "emit_and_close_ready_turns", wrapped)


def test_emit_leaves_the_global_lock_free(
    hook_module: Any,
    fixture_transcript_path: Any,
    fake_langfuse: Any,
    isolated_hook_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The emit must not hold the global lock, or a second session is starved.

    The emit takes seconds on a large transcript. While the global lock covered
    it, every other session waited for the whole emit, gave up and dropped its
    turn without a trace in the log.
    """
    acquired: list[bool] = []

    def try_global_lock() -> None:
        try:
            with hook_module.FileLock(hook_module.LOCK_FILE, timeout_s=0.05):
                acquired.append(True)
        except TimeoutError:
            acquired.append(False)

    _during_emit(hook_module, monkeypatch, try_global_lock)

    emitted = _run_emit(hook_module, fake_langfuse, "session-simple", fixture_transcript_path("simple_turn"))

    assert emitted == 1
    assert acquired == [True], "the global lock was held across the emit"


def test_the_session_lock_is_held_across_the_emit(
    hook_module: Any,
    fixture_transcript_path: Any,
    fake_langfuse: Any,
    isolated_hook_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One session still may not emit twice at the same time.

    Narrowing the global lock must not drop that guarantee: two firings of the
    same session would read one offset and emit the same turn twice.
    """
    acquired: list[bool] = []
    key = hook_module.get_session_state_key("session-simple", str(fixture_transcript_path("simple_turn")))

    def try_session_lock() -> None:
        try:
            with hook_module.FileLock(hook_module.get_session_lock_path(key), timeout_s=0.05):
                acquired.append(True)
        except TimeoutError:
            acquired.append(False)

    _during_emit(hook_module, monkeypatch, try_session_lock)

    _run_emit(hook_module, fake_langfuse, "session-simple", fixture_transcript_path("simple_turn"))

    assert acquired == [False], "a second firing of the same session could emit in parallel"


def test_entry_of_another_session_survives_the_emit(
    hook_module: Any,
    fixture_transcript_path: Any,
    fake_langfuse: Any,
    isolated_hook_state: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The write must re-read the file, or it deletes a concurrent entry.

    The state file holds every session. A session that saves from the copy it
    loaded before its emit writes back a file without the entries that other
    sessions saved meanwhile.
    """
    def other_session_saves() -> None:
        with hook_module.FileLock(hook_module.LOCK_FILE):
            state = hook_module.load_hook_state()
            state["other-session-key"] = {"offset": 42, "updated": "2026-09-10T00:00:00+00:00"}
            hook_module.save_hook_state(state)

    _during_emit(hook_module, monkeypatch, other_session_saves)

    _run_emit(hook_module, fake_langfuse, "session-simple", fixture_transcript_path("simple_turn"))

    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    assert "other-session-key" in state, "the emit deleted the entry of a concurrent session"
    assert len(state) == 2, "the session under test did not save its own entry"


def _age(path: Path, days: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))


def test_a_stale_session_lock_file_is_swept(hook_module: Any, isolated_hook_state: Path) -> None:
    """A run that fails before it saves leaves a lock file with no entry to prune it."""
    orphan = hook_module.get_session_lock_path("a" * 64)
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.touch()
    _age(orphan, 365)

    hook_module.save_hook_state({})

    assert not orphan.exists(), "orphaned lock files accumulate in the state directory forever"


def test_a_fresh_session_lock_file_is_never_swept(
    hook_module: Any, isolated_hook_state: Path
) -> None:
    """The sweep must not delete a lock a running session still holds.

    Deleting it lets the next run of that session create its own file and take a
    second, independent lock, so the session would emit twice in parallel.
    """
    running = hook_module.get_session_lock_path("b" * 64)
    running.parent.mkdir(parents=True, exist_ok=True)
    running.touch()

    hook_module.save_hook_state({})

    assert running.exists(), "the sweep deleted a lock file that a live run may hold"


def test_the_sweep_keeps_the_lock_of_a_live_entry(
    hook_module: Any, isolated_hook_state: Path
) -> None:
    """An old lock file still belonging to a live state entry stays."""
    key = "c" * 64
    lock_path = hook_module.get_session_lock_path(key)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.touch()
    _age(lock_path, 365)

    hook_module.save_hook_state({key: {"offset": 1, "updated": _now_iso()}})

    assert lock_path.exists(), "the sweep removed the lock file of a live session"


def test_the_sweep_leaves_the_global_lock_alone(
    hook_module: Any, isolated_hook_state: Path
) -> None:
    """The global lock file must never be swept."""
    global_lock = Path(hook_module.LOCK_FILE)
    global_lock.parent.mkdir(parents=True, exist_ok=True)
    global_lock.touch()
    _age(global_lock, 365)

    hook_module.save_hook_state({})

    assert global_lock.exists(), "the sweep deleted the global lock file"


def test_main_logs_a_lock_timeout_without_debug(
    hook_module: Any, isolated_hook_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skipped export must say so in the log under default settings.

    The symptom reaches the user as missing data in Langfuse, while the cause
    is local. At debug level the skip left no record at all.
    """
    monkeypatch.setattr(hook_module, "DEBUG", False)
    monkeypatch.setattr(hook_module, "get_langfuse_config", lambda: hook_module.LangfuseConfig(
        "public", "secret", "https://example.test", None
    ))
    monkeypatch.setattr(hook_module, "create_langfuse_client", lambda _config: object())
    monkeypatch.setattr(hook_module, "flush_and_shutdown_langfuse_client", lambda _client: None)

    def refuse(*_args: Any, **_kwargs: Any) -> int:
        raise TimeoutError("could not acquire lock within 2.0s")

    monkeypatch.setattr(hook_module, "emit_new_turns_from_transcript", refuse)

    transcript = isolated_hook_state / "transcript.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)
    transcript.write_text("", encoding="utf-8")
    payload = json.dumps(
        {"session_id": "s", "transcript_path": str(transcript), "hook_event_name": "Stop"}
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))

    assert hook_module.main() == 0

    log_file = Path(hook_module.LOG_FILE)
    log = log_file.read_text(encoding="utf-8") if log_file.exists() else ""
    assert "[INFO]" in log, "the skip left no log line: the cause stays invisible"
    assert "lock timeout" in log
