"""Pins the config precedence: plain env vars beat machine-wide wizard values,
and they beat them as a source tier — a wizard value under one spelling must
not shadow a repo env var under the other (LANGFUSE_X vs CC_LANGFUSE_X)."""

from pathlib import Path
from typing import Any

import pytest


CORE_NAMES = [
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_USER_ID",
]


@pytest.fixture(autouse=True)
def clean_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in CORE_NAMES:
        for key in (
            name,
            f"CC_{name}",
            f"CLAUDE_PLUGIN_OPTION_{name}",
            f"CLAUDE_PLUGIN_OPTION_CC_{name}",
        ):
            monkeypatch.delenv(key, raising=False)
    for key in (
        "LANGFUSE_TAGS",
        "CC_LANGFUSE_TAGS",
        "CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TAGS",
    ):
        monkeypatch.delenv(key, raising=False)


def _read_log(hook_module: Any) -> str:
    log = Path(hook_module.LOG_FILE)
    return log.read_text(encoding="utf-8") if log.exists() else ""


# ----------------- _opt: plain env var wins over wizard -----------------

def test_plain_env_var_wins_over_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == "pk-lf-repo"


def test_wizard_option_is_the_fallback_when_no_env_var_is_set(hook_module: Any, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == "pk-lf-machine"


def test_empty_env_var_falls_through_to_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")

    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == "pk-lf-machine"


def test_neither_set_yields_empty_string(hook_module: Any, monkeypatch):
    assert hook_module._opt("LANGFUSE_PUBLIC_KEY") == ""


# ----------------- source tier beats spelling -----------------

def test_repo_cc_alias_beats_wizard_value(hook_module: Any, monkeypatch):
    # The wizard materializes the plain names on every wizard install; a repo
    # routing via the namespaced spelling must still win.
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_SECRET_KEY", "sk-machine")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_USER_ID", "user-machine")
    monkeypatch.setenv("CC_LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("CC_LANGFUSE_SECRET_KEY", "sk-repo")
    monkeypatch.setenv("LANGFUSE_USER_ID", "user-repo")

    config = hook_module.get_langfuse_config()

    assert config is not None
    assert config.public_key == "pk-lf-repo"
    assert config.secret_key == "sk-repo"
    assert config.user_id == "user-repo"


def test_empty_env_vars_fall_through_to_wizard_for_core_opt(hook_module: Any, monkeypatch):
    # A cleared placeholder ("") must count as unset on both env rungs, or an
    # empty repo value would silently disable a valid wizard config.
    name = "LANGFUSE_PUBLIC_KEY"
    monkeypatch.setenv(name, "")
    monkeypatch.setenv(f"CC_{name}", "")
    monkeypatch.setenv(f"CLAUDE_PLUGIN_OPTION_{name}", "wizard-plain")

    assert hook_module._core_opt(name) == ("wizard-plain", "wizard")


def test_core_opt_returns_empty_pair_when_nothing_is_set(hook_module: Any):
    assert hook_module._core_opt("LANGFUSE_PUBLIC_KEY") == ("", "")


def test_core_opt_resolves_the_full_ladder(hook_module: Any, monkeypatch):
    name = "LANGFUSE_PUBLIC_KEY"
    monkeypatch.setenv(f"CLAUDE_PLUGIN_OPTION_CC_{name}", "wizard-cc")
    assert hook_module._core_opt(name) == ("wizard-cc", "wizard")

    monkeypatch.setenv(f"CLAUDE_PLUGIN_OPTION_{name}", "wizard-plain")
    assert hook_module._core_opt(name) == ("wizard-plain", "wizard")

    monkeypatch.setenv(f"CC_{name}", "env-cc")
    assert hook_module._core_opt(name) == ("env-cc", "env")

    monkeypatch.setenv(name, "env-plain")
    assert hook_module._core_opt(name) == ("env-plain", "env")


# ----------------- mixed-source configs warn -----------------

def test_partial_repo_env_block_warns_mixed_source(hook_module: Any, monkeypatch):
    # Keys from the repo, base URL left to the wizard's machine-wide value:
    # the classic partial env block that 401s against the wrong host.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    config = hook_module.get_langfuse_config()

    assert config is not None
    assert config.host == "https://cloud.langfuse.com"
    log = _read_log(hook_module)
    assert "mixed-source" in log
    assert "base_url from wizard" in log


def test_torn_key_pair_warns_mixed_source(hook_module: Any, monkeypatch):
    # The most dangerous mix: the two keys themselves from different sources.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_SECRET_KEY", "sk-machine")

    config = hook_module.get_langfuse_config()

    assert config is not None
    log = _read_log(hook_module)
    assert "mixed-source" in log
    assert "secret_key from wizard" in log
    assert "base_url from default" in log


def test_wizard_keys_with_env_host_warn_mixed_source(hook_module: Any, monkeypatch):
    # The reverse direction: keys from the wizard, host from the environment.
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_SECRET_KEY", "sk-machine")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example.com")

    config = hook_module.get_langfuse_config()

    assert config is not None
    assert config.host == "https://langfuse.example.com"
    assert "mixed-source" in _read_log(hook_module)


def test_wizard_only_config_does_not_warn(hook_module: Any, monkeypatch):
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_PUBLIC_KEY", "pk-lf-machine")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_SECRET_KEY", "sk-machine")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_LANGFUSE_BASE_URL", "https://cloud.langfuse.com")

    assert hook_module.get_langfuse_config() is not None
    assert "mixed-source" not in _read_log(hook_module)


def test_single_source_config_does_not_warn(hook_module: Any, monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-repo")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example.com")

    assert hook_module.get_langfuse_config() is not None
    assert "mixed-source" not in _read_log(hook_module)


def test_env_keys_with_code_default_host_do_not_warn(hook_module: Any, monkeypatch):
    # No wizard value anywhere: falling back to the built-in default is not
    # a mixed config, it is the documented single-source setup.
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-repo")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-repo")

    config = hook_module.get_langfuse_config()

    assert config is not None
    assert config.host == "https://cloud.langfuse.com"
    assert config.user_id is None
    assert "mixed-source" not in _read_log(hook_module)


# ----------------- CC_LANGFUSE_TAGS: _opt, not _core_opt -----------------

def test_custom_tags_env_var_wins_over_wizard_option(hook_module: Any, monkeypatch):
    monkeypatch.setenv("CC_LANGFUSE_TAGS", "env:repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TAGS", "env:machine")

    assert hook_module._opt("CC_LANGFUSE_TAGS") == "env:repo"


def test_custom_tags_fall_through_to_wizard_when_env_is_empty(hook_module: Any, monkeypatch):
    monkeypatch.setenv("CC_LANGFUSE_TAGS", "")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TAGS", "env:machine")

    assert hook_module._opt("CC_LANGFUSE_TAGS") == "env:machine"


def test_bare_langfuse_tags_env_var_is_not_read(hook_module: Any, monkeypatch):
    """CC_LANGFUSE_TAGS resolves through _opt, not _core_opt, so the un-prefixed
    LANGFUSE_TAGS is deliberately not claimed — the Langfuse SDK does not define
    it, and taking the name would be a namespace land-grab."""
    monkeypatch.setenv("LANGFUSE_TAGS", "env:bare")

    assert hook_module._opt("CC_LANGFUSE_TAGS") == ""
