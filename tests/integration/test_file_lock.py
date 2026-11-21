from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterator

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_PATH = REPO_ROOT / "hooks" / "langfuse_hook.py"
CHILD_TIMEOUT_S = 30.0
# FileLock's deadline uses time.time(), which ticks every 15.6 ms on Windows
# before Python 3.13, so a timeout can fire up to one tick early.
CLOCK_SLACK_S = 0.05


def _assert_refused(hook_module: Any, path: Path) -> None:
    with pytest.raises(TimeoutError):
        with hook_module.FileLock(path, timeout_s=0.05):
            pass


# ----------------- in-process -----------------

def test_a_held_lock_refuses_a_second_handle(hook_module: Any, isolated_hook_state: Path) -> None:
    """Two handles in one process conflict, on Unix and on Windows alike.

    The other contention tests rely on this: they take the second lock in
    the same process instead of starting a subprocess.
    """
    path = isolated_hook_state / "probe.lock"

    with hook_module.FileLock(path) as held:
        assert held.acquired, "the lock was entered without locking anything"
        _assert_refused(hook_module, path)


def test_the_timeout_is_honoured(hook_module: Any, isolated_hook_state: Path) -> None:
    path = isolated_hook_state / "probe.lock"
    timeout_s = 0.3

    with hook_module.FileLock(path):
        start = time.perf_counter()
        with pytest.raises(TimeoutError):
            with hook_module.FileLock(path, timeout_s=timeout_s):
                pass
        elapsed = time.perf_counter() - start

    assert elapsed >= timeout_s - CLOCK_SLACK_S, "gave up before the timeout"
    # Well short of the 2 s default, so timeout_s is the limit in force.
    assert elapsed < 2.0, "waited far past the timeout"


def test_an_exception_in_the_block_releases_the_lock(hook_module: Any, isolated_hook_state: Path) -> None:
    path = isolated_hook_state / "probe.lock"

    with pytest.raises(RuntimeError):
        with hook_module.FileLock(path):
            _assert_refused(hook_module, path)
            raise RuntimeError("boom")

    with hook_module.FileLock(path, timeout_s=0.05) as second:
        assert second.acquired


# ----------------- across processes -----------------

def _reap(child: subprocess.Popen) -> str:
    """Kill child if it is still running, wait for it and return its stderr."""
    if child.poll() is None:
        child.kill()
    return child.communicate(timeout=CHILD_TIMEOUT_S)[1]


@contextlib.contextmanager
def _children(state_dir: Path) -> Iterator[Callable[..., subprocess.Popen]]:
    """Yield a function that starts a child; every child is reaped on exit."""
    started: list[subprocess.Popen] = []

    def spawn(*args: str) -> subprocess.Popen:
        child = subprocess.Popen(
            [sys.executable, __file__, str(state_dir), *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        started.append(child)
        return child

    try:
        yield spawn
    finally:
        for child in started:
            if child.poll() is None:
                child.kill()
        for child in started:
            if not child.stderr.closed:
                child.communicate(timeout=CHILD_TIMEOUT_S)


def _finish(child: subprocess.Popen) -> None:
    _, stderr = child.communicate(timeout=CHILD_TIMEOUT_S)
    assert child.returncode == 0, stderr


def _wait_for(path: Path, child: subprocess.Popen | None = None) -> None:
    """Wait for a signal file; raise if it never appears or child exits first."""
    deadline = time.monotonic() + CHILD_TIMEOUT_S
    while not path.exists():
        if child is not None and child.poll() is not None:
            raise AssertionError(f"child exited with {child.returncode} before {path.name}: {_reap(child)}")
        if time.monotonic() > deadline:
            raise TimeoutError(f"{path.name} did not appear within {CHILD_TIMEOUT_S}s")
        time.sleep(0.01)


def test_a_lock_held_by_another_process_is_refused_until_released(
    hook_module: Any, isolated_hook_state: Path
) -> None:
    lock_path = isolated_hook_state / "probe.lock"
    ready = isolated_hook_state / "ready"
    release = isolated_hook_state / "release"

    with _children(isolated_hook_state) as spawn:
        holder = spawn("hold", str(lock_path), str(ready), str(release))
        _wait_for(ready, holder)
        _assert_refused(hook_module, lock_path)
        release.touch()
        _finish(holder)

    with hook_module.FileLock(lock_path, timeout_s=2.0) as lock:
        assert lock.acquired


def test_a_killed_holder_releases_the_lock(hook_module: Any, isolated_hook_state: Path) -> None:
    """A run that dies while holding the lock must not block later runs."""
    lock_path = isolated_hook_state / "probe.lock"
    ready = isolated_hook_state / "ready"

    with _children(isolated_hook_state) as spawn:
        holder = spawn("hold", str(lock_path), str(ready), str(isolated_hook_state / "never"))
        _wait_for(ready, holder)
        _assert_refused(hook_module, lock_path)
        _reap(holder)

    with hook_module.FileLock(lock_path, timeout_s=2.0) as lock:
        assert lock.acquired


def test_concurrent_processes_lose_no_state_update(hook_module: Any, isolated_hook_state: Path) -> None:
    """Each process increments its own entry; none may overwrite another's.

    The state file holds every session, so each save is a read-modify-write
    of the whole file under the global lock. Without exclusion, a save
    written from a stale read drops the increments other processes made.
    """
    processes, increments = 4, 25
    go = isolated_hook_state / "go"

    with _children(isolated_hook_state) as spawn:
        workers = [
            spawn("increment", f"worker-{i}", str(increments), str(isolated_hook_state / f"ready-{i}"), str(go))
            for i in range(processes)
        ]
        for i, worker in enumerate(workers):
            _wait_for(isolated_hook_state / f"ready-{i}", worker)
        go.touch()
        for worker in workers:
            _finish(worker)

    state = json.loads((isolated_hook_state / "langfuse_state.json").read_text(encoding="utf-8"))
    counts = {f"worker-{i}": state.get(f"worker-{i}", {}).get("n") for i in range(processes)}
    assert counts == {key: increments for key in counts}, "concurrent saves lost updates"


# ----------------- child process -----------------

def _load_hook_in_child(state_dir: str) -> Any:
    """Load the hook with stub SDK modules and its state pinned to state_dir."""
    import importlib.util
    import types

    os.environ["CC_LANGFUSE_STATE_DIR"] = state_dir
    langfuse_module = types.ModuleType("langfuse")
    langfuse_module.Langfuse = type("Langfuse", (), {})
    langfuse_module.propagate_attributes = lambda **_: None
    otel_module = types.ModuleType("opentelemetry")
    otel_module.trace = types.ModuleType("opentelemetry.trace")
    sys.modules.update({
        "langfuse": langfuse_module,
        "opentelemetry": otel_module,
        "opentelemetry.trace": otel_module.trace,
    })

    spec = importlib.util.spec_from_file_location("langfuse_hook_lock_child", HOOK_PATH)
    hook = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = hook
    spec.loader.exec_module(hook)
    if Path(hook.STATE_DIR) != Path(state_dir):
        # Never touch a real state directory.
        sys.exit(3)
    return hook


def _child_main(state_dir: str, command: str, *args: str) -> None:
    hook = _load_hook_in_child(state_dir)
    if command == "hold":
        lock_path, ready, release = args
        with hook.FileLock(Path(lock_path), timeout_s=CHILD_TIMEOUT_S):
            Path(ready).touch()
            _wait_for(Path(release))
    elif command == "increment":
        key, increments, ready, go = args
        Path(ready).touch()
        _wait_for(Path(go))
        for _ in range(int(increments)):
            # A long timeout: this measures exclusion, not lock fairness.
            with hook.FileLock(hook.LOCK_FILE, timeout_s=CHILD_TIMEOUT_S):
                state = hook.load_hook_state()
                entry = state.get(key) if isinstance(state.get(key), dict) else {}
                state[key] = {"n": int(entry.get("n", 0)) + 1}
                hook.save_hook_state(state)


if __name__ == "__main__":
    _child_main(*sys.argv[1:])
