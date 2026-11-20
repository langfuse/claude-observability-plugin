from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# Stands in for the SDK's atexit shutdown, which drains the media upload
# queue with no time limit: against an unreachable host each queued image
# retries with backoff, so a session with screenshots blocks for minutes.
STUB_LANGFUSE = """
import atexit, contextlib, time

class Langfuse:
    def __init__(self, **_):
        atexit.register(time.sleep, 60)

    def flush(self):
        pass

    def shutdown(self):
        pass

@contextlib.contextmanager
def propagate_attributes(**_):
    yield
"""


def test_hook_exits_without_waiting_for_sdk_atexit_drain(tmp_path: Path) -> None:
    stubs = tmp_path / "stubs"
    (stubs / "langfuse").mkdir(parents=True)
    (stubs / "langfuse" / "__init__.py").write_text(STUB_LANGFUSE)
    (stubs / "opentelemetry").mkdir()
    (stubs / "opentelemetry" / "__init__.py").write_text("trace = None\n")

    transcript = tmp_path / "session-1.jsonl"
    transcript.write_text("")
    payload = {"session_id": "session-1", "transcript_path": str(transcript), "hook_event_name": "Stop"}

    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(tmp_path),
        "PYTHONPATH": str(stubs),
        "CC_LANGFUSE_STATE_DIR": str(tmp_path / "state"),
        "LANGFUSE_PUBLIC_KEY": "pk-lf-test",
        "LANGFUSE_SECRET_KEY": "sk-lf-test",
        "LANGFUSE_BASE_URL": "http://127.0.0.1:9",
    }

    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hooks" / "langfuse_hook.py")],
        input=json.dumps(payload),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed = time.monotonic() - started

    assert result.returncode == 0, result.stderr
    assert elapsed < 10, f"hook took {elapsed:.1f}s; the SDK atexit drain was not skipped"
    log_text = (tmp_path / "state" / "langfuse_hook.log").read_text(encoding="utf-8")
    assert "Processed 0 turns" in log_text
