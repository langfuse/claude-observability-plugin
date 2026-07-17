from __future__ import annotations

import hashlib

import pytest


def sha256_trace_id(seed_string: str) -> str:
    return hashlib.sha256(seed_string.encode("utf-8")).hexdigest()[:32]


def test_derive_turn_trace_id_matches_sdk_seed_formula(hook_module):
    assert hook_module.derive_turn_trace_id("my-session", 1) == sha256_trace_id("my-session:1")
    assert hook_module.derive_turn_trace_id("my-session", 12) == sha256_trace_id("my-session:12")


def test_derive_turn_trace_id_differs_per_turn(hook_module):
    first = hook_module.derive_turn_trace_id("my-session", 1)
    second = hook_module.derive_turn_trace_id("my-session", 2)
    assert first != second
    assert len(first) == len(second) == 32
    int(first, 16)
    int(second, 16)


def test_derive_turn_trace_id_falls_back_to_sha256_when_sdk_helper_raises(
    hook_module, monkeypatch: pytest.MonkeyPatch
):
    def _boom(*, seed: str | None = None) -> str:
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(hook_module.Langfuse, "create_trace_id", staticmethod(_boom))
    assert hook_module.derive_turn_trace_id("my-session", 1) == sha256_trace_id("my-session:1")


def test_derive_turn_trace_id_ignores_invalid_sdk_helper_result(
    hook_module, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        hook_module.Langfuse, "create_trace_id", staticmethod(lambda *, seed=None: "not-a-trace-id")
    )
    assert hook_module.derive_turn_trace_id("my-session", 1) == sha256_trace_id("my-session:1")
