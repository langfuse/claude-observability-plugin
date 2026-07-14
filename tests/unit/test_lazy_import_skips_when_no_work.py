from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_hook_module_without_forcing_import():
    """Load a throwaway copy of the hook module, deliberately NOT calling
    _ensure_langfuse_imported() the way the shared `hook_module` fixture does.

    This lets us observe whether main() itself ever triggers the import, which
    is the actual behavior being fixed - the shared fixture forces the import
    upfront (so other tests can poke module.Langfuse directly), which would
    mask a regression here.
    """
    module_path = REPO_ROOT / "hooks" / "langfuse_hook.py"
    spec = importlib.util.spec_from_file_location("langfuse_hook_lazy_import_probe", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_unconfigured_plugin_never_imports_langfuse(monkeypatch):
    # Before this fix, langfuse/opentelemetry were imported at module load
    # time unconditionally - so even a plugin with no Langfuse keys set paid
    # the import cost on every single hook invocation before bailing out.
    module = _load_hook_module_without_forcing_import()
    assert module.Langfuse is None

    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))

    assert module.main() == 0
    assert module.Langfuse is None


def test_missing_transcript_never_imports_langfuse(monkeypatch, tmp_path):
    module = _load_hook_module_without_forcing_import()
    assert module.Langfuse is None

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    missing_transcript = tmp_path / "missing.jsonl"
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(json.dumps({"sessionId": "s1", "transcriptPath": str(missing_transcript)})),
    )

    assert module.main() == 0
    assert module.Langfuse is None


def test_caught_up_session_never_imports_langfuse(monkeypatch, tmp_path):
    module = _load_hook_module_without_forcing_import()
    assert module.Langfuse is None

    state_dir = tmp_path / "claude-state"
    monkeypatch.setattr(module, "STATE_DIR", state_dir)
    monkeypatch.setattr(module, "STATE_FILE", state_dir / "langfuse_state.json")
    monkeypatch.setattr(module, "LOCK_FILE", state_dir / "langfuse_state.lock")
    monkeypatch.setattr(module, "LOG_FILE", state_dir / "langfuse_hook.log")

    transcript = tmp_path / "session.jsonl"
    transcript.write_text('{"type": "user"}\n', encoding="utf-8")

    state = module.load_hook_state()
    key = module.get_session_state_key("s1", str(transcript.resolve()))
    session_state = module.get_session_state(state, key)
    session_state.offset = transcript.stat().st_size
    module.save_session_state(state, key, session_state)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {"sessionId": "s1", "transcriptPath": str(transcript), "hookEventName": "SessionEnd"}
            )
        ),
    )

    assert module.main() == 0
    assert module.Langfuse is None
