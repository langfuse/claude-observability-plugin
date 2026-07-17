"""Pins _opt's config precedence: repo env var over machine-wide wizard value.

Claude Code stores plugin userConfig at user (machine) scope only; a project's
.claude/settings.local.json `env` block is the only per-repo channel. The plain
env var must therefore win, or one machine-wide wizard value would silently
reroute every project's traces to a single Langfuse project.
"""

from typing import Any


def test_plain_env_var_wins_over_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == "pk-lf-repo"


def test_wizard_option_is_the_fallback_when_no_env_var_is_set(hook_module: Any, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == "pk-lf-machine"


def test_empty_env_var_falls_through_to_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == "pk-lf-machine"


def test_neither_set_yields_empty_string(hook_module: Any, monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", raising=False)

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == ""
