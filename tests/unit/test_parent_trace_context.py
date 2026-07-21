from __future__ import annotations

import pytest


VALID_TRACE_ID = "a" * 31 + "b"
VALID_SPAN_ID = "c" * 15 + "d"
VALID_TRACEPARENT = f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01"


class TestParseTraceparent:
    def test_valid_traceparent(self, hook_module):
        assert hook_module.parse_traceparent(VALID_TRACEPARENT) == (VALID_TRACE_ID, VALID_SPAN_ID)

    def test_uppercase_and_whitespace_are_normalized(self, hook_module):
        assert hook_module.parse_traceparent(f"  {VALID_TRACEPARENT.upper()}  ") == (
            VALID_TRACE_ID,
            VALID_SPAN_ID,
        )

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "garbage",
            f"01-{VALID_TRACE_ID}-{VALID_SPAN_ID}-01",  # unsupported version
            f"00-{VALID_TRACE_ID}-{VALID_SPAN_ID}",  # missing flags
            f"00-{'0' * 32}-{VALID_SPAN_ID}-01",  # all-zero trace id
            f"00-{VALID_TRACE_ID}-{'0' * 16}-01",  # all-zero span id
            f"00-{'a' * 31}-{VALID_SPAN_ID}-01",  # short trace id
            f"00-{VALID_TRACE_ID}-{'c' * 15}-01",  # short span id
            f"00-{'g' * 32}-{VALID_SPAN_ID}-01",  # non-hex trace id
        ],
    )
    def test_invalid_traceparent_returns_none(self, hook_module, value):
        assert hook_module.parse_traceparent(value) is None


class TestGetParentTraceContextFromEnv:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch: pytest.MonkeyPatch):
        for name in (
            "CC_LANGFUSE_TRACEPARENT",
            "CC_LANGFUSE_PARENT_TRACE_ID",
            "CC_LANGFUSE_PARENT_SPAN_ID",
        ):
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)

    def test_unset_returns_none_pair(self, hook_module):
        assert hook_module.get_parent_trace_context_from_env() == (None, None)

    def test_traceparent_is_parsed(self, hook_module, monkeypatch):
        monkeypatch.setenv("CC_LANGFUSE_TRACEPARENT", VALID_TRACEPARENT)
        assert hook_module.get_parent_trace_context_from_env() == (VALID_TRACE_ID, VALID_SPAN_ID)

    def test_explicit_pair_is_read(self, hook_module, monkeypatch):
        monkeypatch.setenv("CC_LANGFUSE_PARENT_TRACE_ID", VALID_TRACE_ID.upper())
        monkeypatch.setenv("CC_LANGFUSE_PARENT_SPAN_ID", VALID_SPAN_ID.upper())
        assert hook_module.get_parent_trace_context_from_env() == (VALID_TRACE_ID, VALID_SPAN_ID)

    def test_traceparent_wins_over_explicit_pair(self, hook_module, monkeypatch):
        monkeypatch.setenv("CC_LANGFUSE_TRACEPARENT", VALID_TRACEPARENT)
        monkeypatch.setenv("CC_LANGFUSE_PARENT_TRACE_ID", "1" * 32)
        monkeypatch.setenv("CC_LANGFUSE_PARENT_SPAN_ID", "2" * 16)
        assert hook_module.get_parent_trace_context_from_env() == (VALID_TRACE_ID, VALID_SPAN_ID)

    def test_malformed_traceparent_falls_back_to_pair(self, hook_module, monkeypatch):
        monkeypatch.setenv("CC_LANGFUSE_TRACEPARENT", "not-a-traceparent")
        monkeypatch.setenv("CC_LANGFUSE_PARENT_TRACE_ID", VALID_TRACE_ID)
        monkeypatch.setenv("CC_LANGFUSE_PARENT_SPAN_ID", VALID_SPAN_ID)
        assert hook_module.get_parent_trace_context_from_env() == (VALID_TRACE_ID, VALID_SPAN_ID)

    @pytest.mark.parametrize(
        "trace_id, span_id",
        [
            (VALID_TRACE_ID, None),  # incomplete pair
            (None, VALID_SPAN_ID),  # incomplete pair
            ("nothex", VALID_SPAN_ID),
            (VALID_TRACE_ID, "nothex"),
            ("0" * 32, VALID_SPAN_ID),  # all-zero trace id
            (VALID_TRACE_ID, "0" * 16),  # all-zero span id
        ],
    )
    def test_invalid_pair_returns_none(self, hook_module, monkeypatch, trace_id, span_id):
        if trace_id is not None:
            monkeypatch.setenv("CC_LANGFUSE_PARENT_TRACE_ID", trace_id)
        if span_id is not None:
            monkeypatch.setenv("CC_LANGFUSE_PARENT_SPAN_ID", span_id)
        assert hook_module.get_parent_trace_context_from_env() == (None, None)

    def test_bare_traceparent_env_var_is_ignored(self, hook_module, monkeypatch):
        # Claude Code's native OTel telemetry injects TRACEPARENT into
        # subprocess environments; the hook must not pick it up.
        monkeypatch.setenv("TRACEPARENT", VALID_TRACEPARENT)
        assert hook_module.get_parent_trace_context_from_env() == (None, None)


class TestConfigPrecedence:
    @pytest.fixture(autouse=True)
    def credentials(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
        for name in (
            "CC_LANGFUSE_TRACEPARENT",
            "CC_LANGFUSE_PARENT_TRACE_ID",
            "CC_LANGFUSE_PARENT_SPAN_ID",
            "CC_LANGFUSE_TRACE_SEED",
        ):
            monkeypatch.delenv(name, raising=False)
            monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{name}", raising=False)

    def test_parent_context_disables_trace_seed(self, hook_module, monkeypatch):
        monkeypatch.setenv("CC_LANGFUSE_TRACE_SEED", "my-seed")
        monkeypatch.setenv("CC_LANGFUSE_TRACEPARENT", VALID_TRACEPARENT)
        config = hook_module.get_langfuse_config()
        assert config.trace_seed is None
        assert config.parent_context == (VALID_TRACE_ID, VALID_SPAN_ID)

    def test_trace_seed_stays_without_parent_context(self, hook_module, monkeypatch):
        monkeypatch.setenv("CC_LANGFUSE_TRACE_SEED", "my-seed")
        config = hook_module.get_langfuse_config()
        assert config.trace_seed == "my-seed"
        assert config.parent_context is None
