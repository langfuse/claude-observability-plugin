from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest


KEY_NAMES = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "CC_LANGFUSE_PUBLIC_KEY",
    "CC_LANGFUSE_SECRET_KEY",
]


@pytest.fixture
def clean_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)


def _read_log(hook_module: Any) -> str:
    return Path(hook_module.LOG_FILE).read_text(encoding="utf-8")


def test_missing_keys_are_logged_at_info(hook_module: Any, clean_key_env: None) -> None:
    hook_module.log_missing_langfuse_config()

    log = _read_log(hook_module)
    assert "Langfuse config incomplete" in log
    assert "LANGFUSE_PUBLIC_KEY" in log
    assert "LANGFUSE_SECRET_KEY" in log


def test_partial_keys_name_only_the_missing_one(
    hook_module: Any, clean_key_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")

    hook_module.log_missing_langfuse_config()

    log = _read_log(hook_module)
    assert "missing LANGFUSE_SECRET_KEY (" in log


def test_client_creation_failure_is_logged(hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    class ExplodingLangfuse:
        def __init__(self, **_: Any) -> None:
            raise RuntimeError("boom")

    monkeypatch.setattr(hook_module, "Langfuse", ExplodingLangfuse)
    config = hook_module.LangfuseConfig(public_key="pk", secret_key="sk", host="h", user_id=None)

    assert hook_module.create_langfuse_client(config) is None

    log = _read_log(hook_module)
    assert "Langfuse client creation failed" in log
    assert "RuntimeError" in log
