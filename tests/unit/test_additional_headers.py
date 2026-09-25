"""Pins CC_LANGFUSE_HEADERS_COMMAND: the headers reach the SDK, every failure
mode is fail-open, and header values never land in the log."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest


HEADERS_VAR = "CC_LANGFUSE_HEADERS_COMMAND"


@pytest.fixture(autouse=True)
def clean_headers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (HEADERS_VAR, f"CLAUDE_PLUGIN_OPTION_{HEADERS_VAR}"):
        monkeypatch.delenv(key, raising=False)


def _log_tail(hook_module: Any, offset: int) -> str:
    log = Path(hook_module.LOG_FILE)
    return log.read_text(encoding="utf-8")[offset:] if log.exists() else ""


def _log_offset(hook_module: Any) -> int:
    log = Path(hook_module.LOG_FILE)
    return len(log.read_text(encoding="utf-8")) if log.exists() else 0


def _stub_run(monkeypatch: pytest.MonkeyPatch, hook_module: Any, result: Any) -> list[dict[str, Any]]:
    """Replace subprocess.run with a recorder; `result` is stdout or an exception."""
    calls: list[dict[str, Any]] = []

    def fake_run(cmd: Any, **kwargs: Any) -> Any:
        calls.append({"cmd": cmd, **kwargs})
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=result, stderr="")

    monkeypatch.setattr(hook_module.subprocess, "run", fake_run)
    return calls


# ----------------- resolve_additional_headers -----------------

def test_unset_returns_none_without_running_anything(hook_module: Any, monkeypatch):
    calls = _stub_run(monkeypatch, hook_module, "{}")
    assert hook_module.resolve_additional_headers() is None
    assert calls == []


def test_command_stdout_json_becomes_headers(hook_module: Any, monkeypatch):
    monkeypatch.setenv(HEADERS_VAR, "print-headers")
    calls = _stub_run(monkeypatch, hook_module, json.dumps({"Cookie": "oidc-auth=abc", "X-Extra": 7}))

    assert hook_module.resolve_additional_headers() == {"Cookie": "oidc-auth=abc", "X-Extra": "7"}
    assert calls[0]["cmd"] == "print-headers"
    # The hook's own stdout is the hook protocol, and a wedged helper must not
    # hold the turn open.
    assert calls[0]["capture_output"] is True
    assert calls[0]["timeout"] == hook_module.HEADERS_COMMAND_TIMEOUT


def test_wizard_option_is_read_as_fallback(hook_module: Any, monkeypatch):
    monkeypatch.setenv(f"CLAUDE_PLUGIN_OPTION_{HEADERS_VAR}", "print-headers")
    _stub_run(monkeypatch, hook_module, json.dumps({"Cookie": "c"}))
    assert hook_module.resolve_additional_headers() == {"Cookie": "c"}


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CalledProcessError(1, "print-headers"),
        subprocess.TimeoutExpired("print-headers", 10),
        FileNotFoundError("print-headers"),
        "not json",
        json.dumps(["Cookie", "c"]),
        json.dumps({}),
        "",
    ],
    ids=["nonzero-exit", "timeout", "missing-helper", "bad-json", "not-an-object", "empty-object", "no-output"],
)
def test_every_failure_mode_is_fail_open_and_logged(hook_module: Any, monkeypatch, result):
    monkeypatch.setenv(HEADERS_VAR, "print-headers")
    _stub_run(monkeypatch, hook_module, result)
    offset = _log_offset(hook_module)

    assert hook_module.resolve_additional_headers() is None
    assert HEADERS_VAR in _log_tail(hook_module, offset)


def test_header_values_stay_out_of_the_log(hook_module: Any, monkeypatch):
    monkeypatch.setenv(HEADERS_VAR, "print-headers")
    monkeypatch.setattr(hook_module, "DEBUG", True)
    _stub_run(monkeypatch, hook_module, json.dumps({"Cookie": "oidc-auth=s3cr3t"}))
    offset = _log_offset(hook_module)

    hook_module.resolve_additional_headers()
    assert "s3cr3t" not in _log_tail(hook_module, offset)


def test_non_object_json_is_named_in_the_log(hook_module: Any, monkeypatch):
    # Without the shape check the dict comprehension raises AttributeError, which
    # returns None all the same but tells the user nothing useful.
    monkeypatch.setenv(HEADERS_VAR, "print-headers")
    _stub_run(monkeypatch, hook_module, json.dumps(["Cookie", "c"]))
    offset = _log_offset(hook_module)

    assert hook_module.resolve_additional_headers() is None
    logged = _log_tail(hook_module, offset)
    assert "did not return a JSON object" in logged and "list" in logged


def test_failure_log_omits_the_command_line(hook_module: Any, monkeypatch):
    # str() of a CalledProcessError repeats the command, which may hold a token.
    cmd = "print-headers --token s3cr3t"
    monkeypatch.setenv(HEADERS_VAR, cmd)
    _stub_run(monkeypatch, hook_module, subprocess.CalledProcessError(3, cmd))
    offset = _log_offset(hook_module)

    assert hook_module.resolve_additional_headers() is None
    logged = _log_tail(hook_module, offset)
    assert "s3cr3t" not in logged
    assert "CalledProcessError" in logged and "exit 3" in logged


# ----------------- create_langfuse_client -----------------

def test_headers_are_passed_to_the_sdk(hook_module: Any, monkeypatch):
    monkeypatch.setenv(HEADERS_VAR, "print-headers")
    _stub_run(monkeypatch, hook_module, json.dumps({"Cookie": "oidc-auth=abc"}))
    kwargs: dict[str, Any] = {}

    class RecordingLangfuse:
        def __init__(self, **kw: Any) -> None:
            kwargs.update(kw)

    monkeypatch.setattr(hook_module, "Langfuse", RecordingLangfuse)
    config = hook_module.LangfuseConfig(
        public_key="pk-lf-1", secret_key="sk-lf-1", host="https://proxy.example.com", user_id=None
    )

    assert hook_module.create_langfuse_client(config) is not None
    assert kwargs["additional_headers"] == {"Cookie": "oidc-auth=abc"}


def test_no_command_sends_no_headers(hook_module: Any, monkeypatch):
    kwargs: dict[str, Any] = {}

    class RecordingLangfuse:
        def __init__(self, **kw: Any) -> None:
            kwargs.update(kw)

    monkeypatch.setattr(hook_module, "Langfuse", RecordingLangfuse)
    config = hook_module.LangfuseConfig(
        public_key="pk-lf-1", secret_key="sk-lf-1", host="https://cloud.langfuse.com", user_id=None
    )

    assert hook_module.create_langfuse_client(config) is not None
    assert kwargs["additional_headers"] is None
