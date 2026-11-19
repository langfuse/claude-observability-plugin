from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "hooks" / "run.sh"
HOOK = str(REPO_ROOT / "hooks" / "langfuse_hook.py")

pytestmark = pytest.mark.skipif(
    shutil.which("sh") is None or shutil.which("env") is None,
    reason="the runner needs a POSIX sh and env",
)

# A fake uv whose `tool dir` probe fails the way an old uv fails on a newer
# user config, unless the call passes --no-config and UV_PREVIEW_FEATURES is
# unset. Every other call records its arguments, its preview env and the
# stdin it received.
FAKE_UV = """#!/bin/sh
case " $* " in
  *" tool dir "*)
    case "$UV_MODE" in
      ok) exit 0 ;;
      isolated)
        case " $* " in *" --no-config "*) ;; *) exit 2 ;; esac
        [ -z "${UV_PREVIEW_FEATURES+set}" ] && exit 0
        exit 2 ;;
      *) exit 2 ;;
    esac ;;
esac
{
  echo "uv $*"
  echo "preview=${UV_PREVIEW_FEATURES-unset}"
  echo "stdin=$(cat)"
} > "$RECORD"
"""

FAKE_PYTHON = """#!/bin/sh
{
  echo "python3 $*"
  echo "stdin=$(cat)"
} > "$RECORD"
"""


def _run(tmp_path: Path, uv_mode: str | None) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fakes = {"python3": FAKE_PYTHON}
    if uv_mode is not None:
        fakes["uv"] = FAKE_UV
    for name, body in fakes.items():
        path = bin_dir / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)

    record = tmp_path / "record.txt"
    system_path = os.pathsep.join(
        str(Path(tool).parent) for tool in (shutil.which("sh"), shutil.which("env"), shutil.which("cat"), shutil.which("dirname"))
    )
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{system_path}",
        "RECORD": str(record),
        "UV_MODE": uv_mode or "",
        "UV_PREVIEW_FEATURES": "a-flag-this-uv-does-not-know",
    }
    subprocess.run(["sh", str(RUNNER)], input='{"payload": 1}', text=True, env=env, check=True, timeout=30)
    return record.read_text(encoding="utf-8").splitlines()


def test_uses_configured_uv_when_it_starts(tmp_path: Path) -> None:
    assert _run(tmp_path, "ok") == [
        f"uv run --quiet --script {HOOK}",
        "preview=a-flag-this-uv-does-not-know",
        'stdin={"payload": 1}',
    ]


def test_isolates_uv_from_its_config_when_it_cannot_load_it(tmp_path: Path) -> None:
    assert _run(tmp_path, "isolated") == [
        f"uv run --no-config --quiet --script {HOOK}",
        "preview=unset",
        'stdin={"payload": 1}',
    ]


def test_falls_back_to_python3_when_uv_cannot_start(tmp_path: Path) -> None:
    assert _run(tmp_path, "broken") == [f"python3 {HOOK}", 'stdin={"payload": 1}']


def test_falls_back_to_python3_without_uv(tmp_path: Path) -> None:
    assert _run(tmp_path, None) == [f"python3 {HOOK}", 'stdin={"payload": 1}']
