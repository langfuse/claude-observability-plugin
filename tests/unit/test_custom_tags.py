"""Tests for CC_LANGFUSE_TAG_COMMAND and CC_LANGFUSE_SESSION_LABEL_COMMAND.

Uses the session-scoped ``hook_module`` fixture (see tests/conftest.py). Config is
read into module globals at import time and results are memoized, so each test
patches the relevant globals and resets the caches rather than reimporting.
"""
from __future__ import annotations

import types
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_caches(hook_module: Any) -> Any:
    # The caches are process-global; the hook_module fixture is session-scoped.
    # Reset on both sides so these tests neither inherit nor leak cached state
    # into other test modules that call get_trace_tags.
    hook_module._custom_tags_cache = None
    hook_module._session_label_cache = None
    yield
    hook_module._custom_tags_cache = None
    hook_module._session_label_cache = None


def _dummy_turn() -> Any:
    return types.SimpleNamespace(assistant_msgs=[])


# ---- collect_custom_tags ----

def test_two_tags_from_command(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "printf 'sc-1234\\nlayer:build\\n'")
    assert hook_module.collect_custom_tags() == ["sc-1234", "layer:build"]


def test_blank_and_whitespace(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "printf '  sc-9\\n\\n  \\nfoo \\n'")
    assert hook_module.collect_custom_tags() == ["sc-9", "foo"]


def test_no_command(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "")
    assert hook_module.collect_custom_tags() == []


def test_non_zero_exit_fails_open(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "echo nope; exit 3")
    assert hook_module.collect_custom_tags() == []


def test_timeout_fails_open(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "sleep 5; echo late")
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_TIMEOUT", 1.0)
    assert hook_module.collect_custom_tags() == []


def test_tag_count_cap(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "seq 1 100")
    assert len(hook_module.collect_custom_tags()) == hook_module.MAX_CUSTOM_TAGS


def test_tag_length_cap(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "python3 -c \"print('x'*200)\"")
    assert len(hook_module.collect_custom_tags()[0]) == hook_module.MAX_CUSTOM_TAG_LEN


def test_get_trace_tags_merges_and_dedups(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SKILL_TAGS", False)
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", "printf 'claude-code\\nsc-77\\n'")
    assert hook_module.get_trace_tags(_dummy_turn()) == ["claude-code", "sc-77"]


# ---- session label ----

SID = "173624a3-cda5-47d3-97f5-d1b2c68d8fef"


def test_session_label_prefix_default(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", "echo sc-53855")
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "prefix")
    assert hook_module.apply_session_label(SID) == f"sc-53855/{SID}"


def test_session_label_collapse(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", "echo sc-53855")
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "collapse")
    assert hook_module.apply_session_label(SID) == "sc-53855"


def test_session_label_off(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", "echo sc-53855")
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "off")
    assert hook_module.apply_session_label(SID) == SID


def test_session_label_no_command(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", "")
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "prefix")
    assert hook_module.apply_session_label(SID) == SID


def test_session_label_non_zero_exit_unchanged(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", "echo sc-1; exit 2")
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "prefix")
    assert hook_module.apply_session_label(SID) == SID


def test_session_label_capped(hook_module, monkeypatch):
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", "python3 -c \"print('x'*250)\"")
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "prefix")
    out = hook_module.apply_session_label(SID)
    assert len(out.split("/")[0]) == hook_module.MAX_CUSTOM_TAG_LEN
    assert len(out) <= hook_module.MAX_SESSION_ID_LEN


def test_one_script_feeds_both(hook_module, monkeypatch):
    script = "printf 'sc-53855\\nlayer:build\\n'"
    monkeypatch.setattr(hook_module, "SKILL_TAGS", False)
    monkeypatch.setattr(hook_module, "CUSTOM_TAG_COMMAND", script)
    monkeypatch.setattr(hook_module, "SESSION_LABEL_COMMAND", script)
    monkeypatch.setattr(hook_module, "SESSION_LABEL_MODE", "prefix")
    assert hook_module.get_trace_tags(_dummy_turn()) == ["claude-code", "sc-53855", "layer:build"]
    assert hook_module.apply_session_label(SID) == f"sc-53855/{SID}"
