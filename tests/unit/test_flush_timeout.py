from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest


ENV_NAME = "CC_LANGFUSE_FLUSH_TIMEOUT"


@pytest.fixture
def clean_flush_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_NAME, raising=False)
    monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{ENV_NAME}", raising=False)


def _read_log(hook_module: Any) -> str:
    log_file = Path(hook_module.LOG_FILE)
    return log_file.read_text(encoding="utf-8") if log_file.exists() else ""


# ----------------- resolving the cap -----------------

def test_default_when_unset(hook_module: Any, clean_flush_env: None) -> None:
    assert hook_module.resolve_flush_timeout() == hook_module.FLUSH_TIMEOUT_DEFAULT


def test_env_var_wins(hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NAME, "7.5")

    assert hook_module.resolve_flush_timeout() == 7.5


def test_plugin_user_config_is_the_fallback(
    hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wizard value must reach the hook, like every other CC_LANGFUSE_* option."""
    monkeypatch.setenv(f"CLAUDE_PLUGIN_OPTION_{ENV_NAME}", "30")

    assert hook_module.resolve_flush_timeout() == 30.0


@pytest.mark.parametrize("raw", ["abc", "", "   ", "0", "-1", "nan", "inf", "1e9"])
def test_unusable_values_fall_back_to_the_default(
    hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(ENV_NAME, raw)

    assert hook_module.resolve_flush_timeout() == hook_module.FLUSH_TIMEOUT_DEFAULT


@pytest.mark.parametrize("raw", ["abc", "0", "-1"])
def test_unusable_values_are_logged(
    hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv(ENV_NAME, raw)

    hook_module.resolve_flush_timeout()

    assert ENV_NAME in _read_log(hook_module)


# ----------------- the cap reaches the join -----------------

class RecordingLangfuse:
    def __init__(self) -> None:
        self.flushed = threading.Event()
        self.shutdown_called = threading.Event()

    def flush(self) -> None:
        self.flushed.set()

    def shutdown(self) -> None:
        self.shutdown_called.set()


def test_flush_and_shutdown_uses_the_resolved_cap(
    hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_NAME, "42")
    seen: list[float | None] = []
    real_join = threading.Thread.join

    def recording_join(self: threading.Thread, timeout: float | None = None) -> None:
        seen.append(timeout)
        real_join(self, timeout)

    monkeypatch.setattr(threading.Thread, "join", recording_join)

    hook_module.flush_and_shutdown_langfuse_client(RecordingLangfuse())

    assert 42.0 in seen


def test_malformed_value_still_waits_for_the_flush(
    hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: parsing used to happen inside the function's blanket
    `except Exception`, so a malformed value raised, skipped the join entirely
    and dropped the session — the exact failure the cap is meant to bound."""
    monkeypatch.setenv(ENV_NAME, "not-a-number")
    client = RecordingLangfuse()

    hook_module.flush_and_shutdown_langfuse_client(client)

    assert client.flushed.is_set()
    assert client.shutdown_called.is_set()


def test_none_client_is_a_noop(hook_module: Any, clean_flush_env: None) -> None:
    hook_module.flush_and_shutdown_langfuse_client(None)


def test_unfinished_flush_is_reported(
    hook_module: Any, clean_flush_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hook that gives up must say so; the silent version is what lost sessions."""
    release = threading.Event()

    class StuckLangfuse:
        def flush(self) -> None:
            release.wait(10)

        def shutdown(self) -> None:
            pass

    monkeypatch.setenv(ENV_NAME, "0.05")
    try:
        hook_module.flush_and_shutdown_langfuse_client(StuckLangfuse())
        assert "did not finish within" in _read_log(hook_module)
    finally:
        release.set()
