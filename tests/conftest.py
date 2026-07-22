from __future__ import annotations

import contextlib
import hashlib
import itertools
import importlib.util
import json
import logging
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
        @staticmethod
        def create_trace_id(*, seed: str | None = None) -> str:
            # Mirrors langfuse v4: seeded trace ids are sha256(seed)[:32].
            if seed:
                return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
            return "f" * 32

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

    class TraceFlags(int):
        SAMPLED = 0x01

    class SpanContext:
        def __init__(self, trace_id: int, span_id: int, is_remote: bool, trace_flags: Any = None) -> None:
            self.trace_id = trace_id
            self.span_id = span_id
            self.is_remote = is_remote
            self.trace_flags = trace_flags

    class NonRecordingSpan:
        def __init__(self, span_context: SpanContext) -> None:
            self._span_context = span_context

        def get_span_context(self) -> SpanContext:
            return self._span_context

    def set_span_in_context(span: Any, context: Any = None) -> dict[str, Any]:
        return {"current_span": span}

    trace_module.use_span = use_span
    trace_module.TraceFlags = TraceFlags
    trace_module.SpanContext = SpanContext
    trace_module.NonRecordingSpan = NonRecordingSpan
    trace_module.set_span_in_context = set_span_in_context
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
    def __init__(self, name: str, start_time: int | None, context: Any = None) -> None:
        self.name = name
        self.start_time = start_time
        self.context = context
        self.attributes: dict[str, Any] = {}

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class FakeTracer:
    def start_span(self, *, name: str, start_time: int | None = None, context: Any = None) -> FakeOtelSpan:
        return FakeOtelSpan(name, start_time, context)


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


def _reset_hook_logger(hook_module: Any) -> None:
    # The logger is cached twice (hook module global + Python's process-global
    # registry), so a handler bound to a stale log path would survive across tests.
    hook_module._logger = None
    logger = logging.getLogger("langfuse_hook")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        with contextlib.suppress(Exception):
            handler.close()


@pytest.fixture(autouse=True)
def _isolated_hook_env(tmp_path: Path, hook_module: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    state_dir = tmp_path / "claude-state"
    monkeypatch.setattr(hook_module, "STATE_DIR", state_dir)
    monkeypatch.setattr(hook_module, "STATE_FILE", state_dir / "langfuse_state.json")
    monkeypatch.setattr(hook_module, "LOCK_FILE", state_dir / "langfuse_state.lock")
    monkeypatch.setattr(hook_module, "LOG_FILE", state_dir / "langfuse_hook.log")
    _reset_hook_logger(hook_module)
    yield state_dir
    _reset_hook_logger(hook_module)


@pytest.fixture
def isolated_hook_state(_isolated_hook_env: Path) -> Path:
    return _isolated_hook_env
