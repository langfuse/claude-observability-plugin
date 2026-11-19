"""The hook passes the secret-redaction mask to the Langfuse client."""

from __future__ import annotations

from typing import Any

import pytest


def _config(hook_module: Any) -> Any:
    return hook_module.LangfuseConfig(public_key="pk", secret_key="sk", host="http://x", user_id=None)


class RecordingLangfuse:
    instances: list = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        RecordingLangfuse.instances.append(self)


@pytest.fixture
def recorder(hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> type:
    RecordingLangfuse.instances = []
    monkeypatch.setattr(hook_module, "Langfuse", RecordingLangfuse)
    return RecordingLangfuse


def test_redaction_is_on_by_default(hook_module: Any) -> None:
    assert hook_module.REDACT_SECRETS is True


def test_client_gets_the_mask_when_redaction_is_on(hook_module: Any, recorder: type, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(hook_module, "REDACT_SECRETS", True)
    monkeypatch.setattr(hook_module, "load_secret_redaction_mask", lambda: sentinel)

    client = hook_module.create_langfuse_client(_config(hook_module))

    assert client is recorder.instances[0]
    assert client.kwargs["mask_otel_spans"] is sentinel


def test_client_has_no_mask_when_redaction_is_off(hook_module: Any, recorder: type, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module, "REDACT_SECRETS", False)

    client = hook_module.create_langfuse_client(_config(hook_module))

    assert "mask_otel_spans" not in client.kwargs


def test_tracing_is_disabled_when_the_mask_cannot_load(hook_module: Any, recorder: type, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hook_module, "REDACT_SECRETS", True)
    monkeypatch.setattr(hook_module, "load_secret_redaction_mask", lambda: None)

    assert hook_module.create_langfuse_client(_config(hook_module)) is None
    assert recorder.instances == []


def test_loader_builds_a_mask_from_the_bundled_module(hook_module: Any) -> None:
    import types as pytypes
    import sys

    fake_types = pytypes.ModuleType("langfuse.types")
    fake_types.OtelSpanPatch = lambda **kw: kw
    fake_types.MaskOtelSpansResult = lambda **kw: kw
    previous = sys.modules.get("langfuse.types")
    sys.modules["langfuse.types"] = fake_types
    try:
        mask = hook_module.load_secret_redaction_mask()
    finally:
        if previous is None:
            sys.modules.pop("langfuse.types", None)
        else:
            sys.modules["langfuse.types"] = previous

    assert callable(mask)


def test_loader_failure_is_logged(hook_module: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(hook_module, "__file__", str(tmp_path / "langfuse_hook.py"))

    assert hook_module.load_secret_redaction_mask() is None
    assert "Secret redaction unavailable" in hook_module.LOG_FILE.read_text()
