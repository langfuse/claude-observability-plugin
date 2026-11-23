"""Commit-after-flush and idempotent re-emission (issue #103).

A firing whose export is not confirmed must leave the state untouched so
the next firing re-emits the same turns, and re-emission must reproduce the
same span ids so Langfuse upserts instead of duplicating.
"""

from __future__ import annotations

import itertools
import json
import threading
import types
from typing import Any

import pytest

from tests.conftest import FakeLangfuse, FakeObservation, FakeOtelSpan, _ACTIVE_SPAN_STACK


# ---- A fake SDK that assigns span ids the way the real tracer would ----
_UNSEEDED = itertools.count(1)
UNSEEDED_SPANS: list = []

class _Ctx:
    def __init__(self, trace_id: int, span_id: int) -> None:
        self.trace_id = trace_id
        self.span_id = span_id


class SeededSpan(FakeOtelSpan):
    def __init__(self, hook: Any, name: str, start_time: int | None, context: Any = None) -> None:
        super().__init__(name, start_time, context)
        seed = hook._SPAN_ID_SEED.get()
        if seed:
            self.span_id = hook.span_id_from_seed(seed)
        else:
            self.span_id = next(_UNSEEDED)
            UNSEEDED_SPANS.append(name)
        if self.parent is not None:
            trace_id = self.parent.get_span_context().trace_id
        elif isinstance(context, dict) and context.get("current_span") is not None:
            trace_id = context["current_span"].get_span_context().trace_id
        else:
            trace_id = 0xABC
        self._ctx = _Ctx(trace_id, self.span_id)

    def get_span_context(self) -> _Ctx:
        return self._ctx


class SeededTracer:
    def __init__(self, hook: Any) -> None:
        self.hook = hook

    def start_span(self, *, name: str, start_time: int | None = None, context: Any = None) -> SeededSpan:
        return SeededSpan(self.hook, name, start_time, context)


class SeededLangfuse(FakeLangfuse):
    def __init__(self, hook: Any) -> None:
        super().__init__()
        self._otel_tracer = SeededTracer(hook)

    def _create_observation_from_otel_span(self, *, otel_span: Any, as_type: str, **kwargs: Any) -> FakeObservation:
        observation = super()._create_observation_from_otel_span(otel_span=otel_span, as_type=as_type, **kwargs)
        observation.id = f"{otel_span.span_id:016x}"
        observation.trace_id = f"{otel_span.get_span_context().trace_id:032x}"
        return observation


@pytest.fixture
def seeded_langfuse(hook_module: Any) -> SeededLangfuse:
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    _ACTIVE_SPAN_STACK.clear()
    UNSEEDED_SPANS.clear()
    return SeededLangfuse(hook_module)


def _config(hook_module: Any) -> Any:
    return hook_module.LangfuseConfig("public", "secret", "https://example.test", "user-1")


def _state(state_dir: Any) -> dict:
    path = state_dir / "langfuse_state.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


@pytest.mark.parametrize("fixture_name", ["tool_turn", "nested_subagents", "workflow_subagents"])
def test_unconfirmed_export_leaves_state_untouched_and_retry_is_idempotent(
    hook_module, fixture_transcript_path, seeded_langfuse, isolated_hook_state, fixture_name,
):
    transcript = fixture_transcript_path(fixture_name)
    config = _config(hook_module)

    # Firing 1: export fails -> nothing committed.
    emitted_first = hook_module.emit_new_turns_from_transcript(
        seeded_langfuse, config, "sess", transcript,
        flush_deferred_agent_turns=True, commit_gate=lambda: False,
    )
    assert emitted_first >= 1
    assert _state(isolated_hook_state) == {}
    first_ids = [(o.name, o.id, o.trace_id) for o in seeded_langfuse.observations]

    # Firing 2 (a new hook process): same turns re-emitted, export confirmed.
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    seeded_langfuse.observations.clear()
    emitted_second = hook_module.emit_new_turns_from_transcript(
        seeded_langfuse, config, "sess", transcript,
        flush_deferred_agent_turns=True, commit_gate=lambda: True,
    )
    assert emitted_second == emitted_first
    second_ids = [(o.name, o.id, o.trace_id) for o in seeded_langfuse.observations]

    # Every span got a deterministic id, and the same one both times.
    assert UNSEEDED_SPANS == []
    assert second_ids == first_ids
    assert len({obs_id for _, obs_id, _ in second_ids}) == len(second_ids), "span ids must be unique"
    assert next(iter(_state(isolated_hook_state).values()))["turn_count"] == emitted_first

    # Firing 3: nothing new -> nothing re-emitted.
    seeded_langfuse.observations.clear()
    assert hook_module.emit_new_turns_from_transcript(
        seeded_langfuse, config, "sess", transcript,
        flush_deferred_agent_turns=True, commit_gate=lambda: True,
    ) == 0
    assert seeded_langfuse.observations == []


def test_confirmed_export_commits_state(hook_module, fixture_transcript_path, fake_langfuse, isolated_hook_state):
    calls = []
    emitted = hook_module.emit_new_turns_from_transcript(
        fake_langfuse, _config(hook_module), "sess", fixture_transcript_path("simple_turn"),
        flush_deferred_agent_turns=True, commit_gate=lambda: calls.append(1) or True,
    )
    assert emitted == 1 and calls == [1]
    assert next(iter(_state(isolated_hook_state).values()))["turn_count"] == 1


# ---- Span seeds ----
def test_span_seed_is_stable_and_disambiguates_identical_siblings(hook_module):
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    kwargs = {"input": {"file": "a.py"}, "metadata": {"tool_id": "toolu_1", "output_meta": "x"}}
    seed = lambda: hook_module.build_span_seed(  # noqa: E731
        trace_id_hex="a" * 32, parent_span_id_hex="b" * 16, name="Read",
        as_type="tool", start_ns=1, obs_kwargs=kwargs,
    )
    first, second = seed(), seed()
    assert first != second  # identical siblings in one firing get distinct ids
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    assert seed() == first  # a new process reproduces the sequence

    # Volatile metadata does not change identity; identity metadata does.
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    kwargs["metadata"]["output_meta"] = "changed"
    assert seed() == first
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    kwargs["metadata"]["tool_id"] = "toolu_2"
    assert seed() != first


def test_span_seed_ignores_object_reprs(hook_module):
    class Media:  # repr would embed a memory address
        pass

    hook_module._SPAN_SEED_OCCURRENCES.clear()
    a = hook_module.build_span_seed(trace_id_hex="a" * 32, parent_span_id_hex=None, name="g",
                                    as_type="generation", start_ns=1, obs_kwargs={"input": [Media()]})
    hook_module._SPAN_SEED_OCCURRENCES.clear()
    b = hook_module.build_span_seed(trace_id_hex="a" * 32, parent_span_id_hex=None, name="g",
                                    as_type="generation", start_ns=1, obs_kwargs={"input": [Media()]})
    assert a == b


def test_no_trace_id_means_no_seed(hook_module):
    assert hook_module.build_span_seed(trace_id_hex=None, parent_span_id_hex=None, name="x",
                                       as_type="span", start_ns=None, obs_kwargs={}) is None


def test_phantom_parent_is_deterministic(hook_module, fake_langfuse):
    a = hook_module.remote_parent(fake_langfuse, "sess", "uuid-1")
    b = hook_module.remote_parent(fake_langfuse, "sess", "uuid-1")
    assert a.get_span_context().span_id == b.get_span_context().span_id != 0


# ---- confirm_export ----
class _Provider:
    def __init__(self, result: bool = True, block: threading.Event | None = None) -> None:
        self.result = result
        self.block = block

    def force_flush(self, timeout_millis: int) -> bool:
        if self.block is not None:
            self.block.wait()
        return self.result


def _client(provider: _Provider) -> Any:
    return types.SimpleNamespace(_resources=types.SimpleNamespace(tracer_provider=provider), flush=lambda: None)


@pytest.fixture
def fresh_ledger(hook_module, monkeypatch):
    ledger = hook_module.ExportLedger()
    ledger.available = True
    monkeypatch.setattr(hook_module, "EXPORT_LEDGER", ledger)
    return ledger


def test_confirm_export_success(hook_module, fresh_ledger):
    fresh_ledger.record(10, ok=True)
    assert hook_module.confirm_export(_client(_Provider(True)), timeout_s=1) is True


def test_confirm_export_fails_on_exporter_failure(hook_module, fresh_ledger):
    fresh_ledger.record(10, ok=True)
    fresh_ledger.record(3, ok=False)
    assert hook_module.confirm_export(_client(_Provider(True)), timeout_s=1) is False


def test_confirm_export_fails_when_sdk_flush_times_out(hook_module, fresh_ledger):
    assert hook_module.confirm_export(_client(_Provider(False)), timeout_s=1) is False


def test_confirm_export_fails_when_flush_hangs(hook_module, fresh_ledger):
    release = threading.Event()
    try:
        assert hook_module.confirm_export(_client(_Provider(True, block=release)), timeout_s=0.05) is False
    finally:
        release.set()


# ---- Request splitting ----
def test_split_into_requests(hook_module):
    sizes = {"a": 3, "b": 3, "c": 3, "big": 10, "huge": 100, "d": 1}
    chunks, oversized = hook_module.split_into_requests(
        ["a", "b", "c", "big", "huge", "d"], sizes.__getitem__, max_bytes=7, hard_limit=50,
    )
    assert chunks == [["a", "b"], ["c"], ["big"], ["d"]]
    assert oversized == [("huge", 100)]


# ---- Detached SessionEnd worker ----
def test_spawn_detached_worker(hook_module, monkeypatch):
    import subprocess

    captured: dict = {}

    class FakeStdin:
        def write(self, data: bytes) -> None:
            captured["stdin"] = data

        def close(self) -> None:
            captured["closed"] = True

    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            self.stdin = FakeStdin()

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    monkeypatch.delenv("CC_LANGFUSE_NO_DETACH", raising=False)
    payload = {"hook_event_name": "SessionEnd", "session_id": "s"}
    assert hook_module.spawn_detached_worker(payload) is True
    assert json.loads(captured["stdin"]) == payload and captured["closed"]
    assert captured["kwargs"]["env"][hook_module.DETACHED_WORKER_ENV] == "1"
    assert captured["kwargs"].get("start_new_session") or captured["kwargs"].get("creationflags")


def test_spawn_detached_worker_opt_out(hook_module, monkeypatch):
    monkeypatch.setenv("CC_LANGFUSE_NO_DETACH", "true")
    assert hook_module.spawn_detached_worker({}) is False
