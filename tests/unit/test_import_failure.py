from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_import_failure_logs_and_exits_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Exec the hook as a fresh module: the autouse isolation fixture patches the
    # session module's constants, so home must be redirected for this re-import.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setitem(sys.modules, "langfuse", None)

    spec = importlib.util.spec_from_file_location(
        "langfuse_hook_import_probe", REPO_ROOT / "hooks" / "langfuse_hook.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)

    with pytest.raises(SystemExit) as excinfo:
        spec.loader.exec_module(module)

    assert excinfo.value.code == 0
    log_text = (tmp_path / ".claude" / "state" / "langfuse_hook.log").read_text(encoding="utf-8")
    assert "langfuse import failed" in log_text
    assert sys.version.split()[0] in log_text
    assert sys.executable in log_text
    assert "PATH=" in log_text
    assert "Hint: uv was not found on this PATH" in log_text
