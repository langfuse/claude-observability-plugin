from __future__ import annotations

import contextlib
import hashlib
import itertools
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any, Iterator

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "transcripts"


def _install_langfuse_stubs() -> None:
    langfuse_module = types.ModuleType("langfuse")

    class Langfuse:
        pass

    @contextlib.contextmanager
    def propagate_attributes(**_: Any) -> Iterator[None]:
        yield

    langfuse_module.Langfuse = Langfuse
    langfuse_module.propagate_attributes = propagate_attributes
    sys.modules["langfuse"] = langfuse_module

    opentelemetry_module = types.ModuleType("opentelemetry")
    trace_module = types.ModuleType("opentelemetry.trace")

    @contextlib.contextmanager
    def use_span(*_: Any, **__: Any) -> Iterator[None]:
        yield

    class SpanContext:
        def __init__(self, *, trace_id: int, span_id: int, trace_flags: int, is_remote: bool) -> None:
            self.trace_id = trace_id
            self.span_id = span_id
            self.trace_flags = trace_flags
            self.is_remote = is_remote

    class TraceFlags(int):
        pass

    class NonRecordingSpan:
        def __init__(self, context: SpanContext) -> None:
            self._context = context

        def get_span_context(self) -> SpanContext:
            return self._context

    trace_module.use_span = use_span
    trace_module.SpanContext = SpanContext
    trace_module.TraceFlags = TraceFlags
    trace_module.NonRecordingSpan = NonRecordingSpan
    opentelemetry_module.trace = trace_module
    sys.modules["opentelemetry"] = opentelemetry_module
    sys.modules["opentelemetry.trace"] = trace_module


@pytest.fixture(scope="session")
def hook_module() -> Any:
    _install_langfuse_stubs()
    module_path = REPO_ROOT / "hooks" / "langfuse_hook.py"
    spec = importlib.util.spec_from_file_location("langfuse_hook_under_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def fixture_transcript_path() -> Any:
    def _path(name: str) -> Path:
        return FIXTURE_ROOT / name / "transcript.jsonl"

    return _path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def read_fixture_jsonl() -> Any:
    return read_jsonl


class FakeOtelSpan:
    def __init__(self, name: str, start_time: int | None) -> None:
        self.name = name
        self.start_time = start_time
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class FakeTracer:
    def start_span(self, *, name: str, start_time: int | None = None) -> FakeOtelSpan:
        return FakeOtelSpan(name, start_time)


_fake_observation_counter = itertools.count(1)


class FakeObservation:
    def __init__(self, otel_span: FakeOtelSpan, as_type: str, kwargs: dict[str, Any]) -> None:
        self._otel_span = otel_span
        self.name = otel_span.name
        self.as_type = as_type
        self.kwargs = kwargs
        self.output: Any = None
        self.end_time: int | None = None
        # Mirror the SDK's hex id/trace_id attributes (span.py sets both).
        n = next(_fake_observation_counter)
        self.id = f"{n:016x}"
        self.trace_id = f"{n:032x}"

    def update(self, **kwargs: Any) -> None:
        if "output" in kwargs:
            self.output = kwargs["output"]
        self.kwargs.update(kwargs)

    def end(self, *, end_time: int | None = None) -> None:
        self.end_time = end_time


class FakeLangfuse:
    def __init__(self) -> None:
        self._otel_tracer = FakeTracer()
        self.observations: list[FakeObservation] = []

    @staticmethod
    def create_trace_id(*, seed: str | None = None) -> str:
        # Mirrors SDK 4.x: sha256(seed).digest()[:16].hex()
        assert seed, "tests always pass a seed"
        return hashlib.sha256(seed.encode("utf-8")).digest()[:16].hex()

    def _create_observation_from_otel_span(
        self,
        *,
        otel_span: FakeOtelSpan,
        as_type: str,
        **kwargs: Any,
    ) -> FakeObservation:
        observation = FakeObservation(otel_span, as_type, kwargs)
        self.observations.append(observation)
        return observation


@pytest.fixture
def fake_langfuse() -> FakeLangfuse:
    return FakeLangfuse()


@pytest.fixture(autouse=True)
def recorded_ingestion_events(hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Keep the suite hermetic: the post-lock ingestion POST never reaches the
    network; tests can assert on the recorded span-update/trace-create events."""
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(hook_module, "post_ingestion_events", lambda config, events: sent.extend(events))
    return sent


@pytest.fixture
def isolated_hook_state(tmp_path: Path, hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> Path:
    state_dir = tmp_path / "claude-state"
    monkeypatch.setattr(hook_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(hook_module, "STATE_FILE", state_dir / "langfuse_state.json")
    monkeypatch.setattr(hook_module, "LOCK_FILE", state_dir / "langfuse_state.lock")
    monkeypatch.setattr(hook_module, "LOG_FILE", state_dir / "langfuse_hook.log")
    return state_dir
