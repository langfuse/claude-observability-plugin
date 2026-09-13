"""dotfiles#214: the mask_otel_spans redaction masker.

Covers the three redaction rules (PEM, labeled credential assignment, unlabeled
high-entropy token), the structural-key skip list, per-attribute error
containment, and the fail-closed client-creation gate.
"""

from types import SimpleNamespace
from typing import Any

import pytest


# ----------------- control string: must pass through unchanged -----------------

CONTROL_STRING = (
    "Ran pytest -q across 271 tests in the observability plugin; all green. "
    "The user asked to bump max_tokens to 8000 and input_tokens stayed at 512."
)


def test_control_string_is_untouched(hook_module: Any):
    assert hook_module._redact_string(CONTROL_STRING) == CONTROL_STRING


# ----------------- PEM blocks -----------------

def test_full_pem_block_is_redacted(hook_module: Any):
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = f"here is a key:\n{pem}\nthanks"
    redacted = hook_module._redact_string(text)
    assert "[REDACTED:pem]" in redacted
    assert "MIIEowIBAAKCAQEA" not in redacted
    assert "here is a key:" in redacted and "thanks" in redacted


def test_truncated_pem_with_no_end_line_is_still_redacted(hook_module: Any):
    # e.g. cut off by an earlier MAX_CHARS truncation before the END line.
    text = "-----BEGIN PRIVATE KEY-----\nMIIEowIBAAKCAQEAsomekeybytesnoend"
    redacted = hook_module._redact_string(text)
    assert redacted == "[REDACTED:pem]"


# ----------------- labeled credential assignment -----------------

@pytest.mark.parametrize(
    "text",
    [
        'api_key = "sk-live-abcdEFGH12345678"',
        "api_key: sk-live-abcdEFGH12345678",
        '"api_key": "sk-live-abcdEFGH12345678"',
        "GITHUB_TOKEN=ghp_abcdEFGH12345678",
        "password: 'Sup3rSecretValue!'",
    ],
)
def test_labeled_credential_value_is_redacted(hook_module: Any, text: str):
    redacted = hook_module._redact_string(text)
    assert "[REDACTED:credential]" in redacted
    assert "sk-live" not in redacted
    assert "ghp_" not in redacted
    assert "Sup3rSecretValue" not in redacted


def test_credential_label_itself_is_preserved(hook_module: Any):
    redacted = hook_module._redact_string('"api_key": "sk-live-abcdEFGH12345678"')
    assert redacted == '"api_key": "[REDACTED:credential]"'


@pytest.mark.parametrize(
    "text",
    [
        "max_tokens=8000",
        "input_tokens: 512",
        "output_tokens=128",
    ],
)
def test_tokens_plural_fields_are_not_mistaken_for_a_credential(hook_module: Any, text: str):
    assert hook_module._redact_string(text) == text


# ----------------- unlabeled high-entropy fallback -----------------

def test_unlabeled_high_entropy_token_is_redacted(hook_module: Any):
    token = "aG3x9Qz1mP7vN0kR4tY8wB2cD6fJ5sL="  # mixed-case + digits + '='
    text = f"exported token {token} into the environment"
    redacted = hook_module._redact_string(text)
    assert "[REDACTED:entropy]" in redacted
    assert token not in redacted


def test_low_entropy_long_word_is_not_redacted(hook_module: Any):
    # 24+ chars, single character class (lowercase only) -> below the class-count floor.
    text = "supercalifragilisticexpialidocious is not a secret"
    assert hook_module._redact_string(text) == text


# ----------------- attribute-value dispatch -----------------

def test_string_sequence_attribute_is_redacted_element_wise(hook_module: Any):
    value = ["safe tag", 'api_key: "sk-live-abcdEFGH12345678"']
    result = hook_module._redact_attribute_value(value)
    assert result[0] == "safe tag"
    assert "[REDACTED:credential]" in result[1]
    assert isinstance(result, list)


def test_non_string_attribute_passes_through(hook_module: Any):
    assert hook_module._redact_attribute_value(42) == 42
    assert hook_module._redact_attribute_value(True) is True
    assert hook_module._redact_attribute_value([1, 2, 3]) == [1, 2, 3]


# ----------------- mask_otel_spans: structural skip list -----------------

def _span(attributes: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(attributes=attributes)


def test_structural_keys_are_left_out_of_the_patch(hook_module: Any):
    params = SimpleNamespace(
        spans={
            "s1": _span(
                {
                    "langfuse.observation.type": "generation",
                    "langfuse.observation.usage_details": {"input": 10},
                    "langfuse.environment": "mini",
                    "langfuse.internal.as_root": True,
                    "langfuse.observation.output": 'token: "sk-live-abcdEFGH12345678"',
                }
            )
        }
    )
    result = hook_module.mask_otel_spans(params=params)
    patch = result.span_patches["s1"]
    assert set(patch.set_attributes) == {"langfuse.observation.output"}
    assert "[REDACTED:credential]" in patch.set_attributes["langfuse.observation.output"]


def test_span_with_nothing_to_redact_gets_no_patch(hook_module: Any):
    params = SimpleNamespace(spans={"s1": _span({"langfuse.observation.type": "span"})})
    result = hook_module.mask_otel_spans(params=params)
    assert result.span_patches == {}


# ----------------- per-attribute error containment -----------------

def test_one_bad_attribute_becomes_a_marker_without_dropping_the_span(hook_module: Any, monkeypatch):
    def _boom(value: Any) -> Any:
        if value == "trigger":
            raise RuntimeError("boom")
        return hook_module._redact_string(value) if isinstance(value, str) else value

    monkeypatch.setattr(hook_module, "_redact_attribute_value", _boom)
    params = SimpleNamespace(
        spans={"s1": _span({"gen_ai.tool.call.arguments": "trigger", "gen_ai.tool.call.result": "fine"})}
    )
    result = hook_module.mask_otel_spans(params=params)
    patch = result.span_patches["s1"]
    assert patch.set_attributes["gen_ai.tool.call.arguments"] == "[REDACTED:masker-error]"
    assert "gen_ai.tool.call.result" not in patch.set_attributes


# ----------------- fail-closed client creation -----------------

def _config(hook_module: Any):
    return hook_module.LangfuseConfig(public_key="pk", secret_key="sk", host="http://x", user_id=None)


def test_redact_on_but_sdk_lacks_mask_types_refuses_to_create_a_client(hook_module: Any, monkeypatch):
    monkeypatch.setattr(hook_module, "REDACT_SECRETS", True)
    monkeypatch.setattr(hook_module, "_MASK_TYPES_AVAILABLE", False)
    calls = []
    monkeypatch.setattr(hook_module, "Langfuse", lambda **kwargs: calls.append(kwargs))

    assert hook_module.create_langfuse_client(_config(hook_module)) is None
    assert calls == []


def test_redact_on_and_available_wires_the_masker_into_the_client(hook_module: Any, monkeypatch):
    monkeypatch.setattr(hook_module, "REDACT_SECRETS", True)
    monkeypatch.setattr(hook_module, "_MASK_TYPES_AVAILABLE", True)
    captured = {}

    def _fake_ctor(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(hook_module, "Langfuse", _fake_ctor)

    assert hook_module.create_langfuse_client(_config(hook_module)) is not None
    assert captured["mask_otel_spans"] is hook_module.mask_otel_spans


def test_redact_off_omits_the_masker(hook_module: Any, monkeypatch):
    monkeypatch.setattr(hook_module, "REDACT_SECRETS", False)
    captured = {}

    def _fake_ctor(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(hook_module, "Langfuse", _fake_ctor)

    hook_module.create_langfuse_client(_config(hook_module))
    assert captured["mask_otel_spans"] is None
