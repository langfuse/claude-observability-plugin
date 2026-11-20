"""Tests for hooks/secret_redaction.py.

Fake credentials are assembled from fragments at runtime so the repository
never contains a literal that secret scanners or push protection would flag.
"""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "hooks" / "secret_redaction.py"
_spec = importlib.util.spec_from_file_location("secret_redaction_under_test", MODULE_PATH)
sr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sr)


def j(*parts: str) -> str:
    return "".join(parts)


HEX64 = "0123456789abcdef" * 4
B62 = "Ab3dE5gH7jK9mN1pQ3sT5vW7yZ9bC1dF3hJ5kL7nP9rS"

FAKES = {
    "aws_access_key_id": j("AKIA", "Q7Z3EXAMPLE4R2LM"),
    "github_token": j("gh", "p_", B62[:36]),
    "gitlab_token": j("gl", "pat-", B62[:24]),
    "slack_token": j("xo", "xb-", "1234567890-", B62[:20]),
    "langfuse_secret_key": j("sk", "-lf-", "0f1e2d3c-4b5a-6978-8a9b-0c1d2e3f4a5b"),
    "anthropic_api_key": j("sk", "-ant-", "api03-", B62[:40]),
    "openai_api_key": j("sk", "-proj-", B62[:40]),
    "google_api_key": j("AI", "za", B62[:35]),
    "stripe_key": j("sk", "_live_", B62[:28]),
    "npm_token": j("np", "m_", B62[:36]),
    "slack_webhook": j("https://hooks.", "slack.com/services/", "T00000000/B00000000/", B62[:24]),
    "pypi_token": j("py", "pi-", "AgEIcHlwaS5vcmc", B62, B62[:10]),
    "atlassian_api_token": j("ATA", "TT3", B62[:32]),
    "jwt": j("ey", "JhbGciOiJIUzI1NiJ9", ".", "ey", "JzdWIiOiIxMjM0NTY3ODkwIn0", ".", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"),
}


def assert_clean(text: str, secret: str) -> None:
    assert secret not in text


@pytest.mark.parametrize("rule,secret", sorted(FAKES.items()))
def test_each_token_format_is_redacted(rule: str, secret: str) -> None:
    text = f"output: {secret} done"
    redacted, hits = sr.redact_text(text)
    assert_clean(redacted, secret)
    assert hits[rule] == 1
    assert f"[REDACTED:{rule}]" in redacted
    assert redacted.startswith("output: ") and redacted.endswith(" done")


def test_private_key_block_is_redacted_whole() -> None:
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nBKcwggSjAgEAAoIBAQC7"
    block = j("-----BEGIN ", "RSA PRIVATE KEY-----\n", body, "\n-----END ", "RSA PRIVATE KEY-----")
    redacted, hits = sr.redact_text(f"cat key.pem\n{block}\nok")
    assert body not in redacted
    assert hits["private_key"] == 1
    assert redacted.endswith("\nok")


def test_aws_secret_access_key_keeps_the_key_name() -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    redacted, hits = sr.redact_text(f"aws_secret_access_key = {secret}")
    assert_clean(redacted, secret)
    assert redacted.startswith("aws_secret_access_key = [REDACTED:")
    assert sum(hits.values()) == 1


def test_bearer_and_basic_auth_keep_the_scheme() -> None:
    token = B62[:32]
    redacted, _ = sr.redact_text(f"Authorization: Bearer {token}")
    assert redacted == "Authorization: Bearer [REDACTED:bearer_token]"
    basic = "dXNlcjpzdXBlcnNlY3JldHBhc3M="
    redacted, _ = sr.redact_text(f"Authorization: Basic {basic}")
    assert redacted == "Authorization: Basic [REDACTED:basic_auth]"


def test_url_credentials_keep_user_and_host() -> None:
    redacted, hits = sr.redact_text("postgres://app:S3cretPassw0rd@db.internal:5432/app")
    assert redacted == "postgres://app:[REDACTED:url_credentials]@db.internal:5432/app"
    assert hits["url_credentials"] == 1


SESSION_TOKEN = j("IQoJb3JpZ2luX2Vj", "EXAMPLE", B62[:40], "%2F", B62[:20], "%3D")
SIGNATURE = HEX64
CREDENTIAL = j("AS", "IAQ7Z3EXAMPLE4R2LM", "%2F20261119%2Fus-east-1%2Fs3%2Faws4_request")
PRESIGNED = (
    "https://bucket.s3.us-east-1.amazonaws.com/media/obj.png"
    "?X-Amz-Algorithm=AWS4-HMAC-SHA256"
    f"&X-Amz-Credential={CREDENTIAL}"
    "&X-Amz-Date=20261119T060000Z&X-Amz-Expires=3600"
    f"&X-Amz-Security-Token={SESSION_TOKEN}"
    "&X-Amz-SignedHeaders=host"
    f"&X-Amz-Signature={SIGNATURE}"
)


def test_presigned_url_redacts_token_signature_and_credential() -> None:
    redacted, hits = sr.redact_text(f"PUT {PRESIGNED} -> 403")
    for secret in (SESSION_TOKEN, SIGNATURE, CREDENTIAL):
        assert_clean(redacted, secret)
    assert hits["aws_presigned_url"] == 3
    # Non-secret parts stay readable for debugging.
    assert "bucket.s3.us-east-1.amazonaws.com/media/obj.png" in redacted
    assert "X-Amz-Expires=3600" in redacted
    assert redacted.endswith(" -> 403")


def test_presigned_url_inside_json_stays_valid_json() -> None:
    payload = json.dumps({"stderr": f"upload failed: {PRESIGNED}"})
    escaped = payload.replace("&", "\\u0026")
    for value in (payload, escaped):
        redacted, hits = sr.redact_attribute_value(value)
        assert SESSION_TOKEN not in redacted and SIGNATURE not in redacted
        assert hits["aws_presigned_url"] == 3
        json.loads(redacted)


def test_security_token_header_is_redacted() -> None:
    redacted, hits = sr.redact_text(f"x-amz-security-token: {SESSION_TOKEN}")
    assert redacted == "x-amz-security-token: [REDACTED:aws_presigned_url]"
    assert hits["aws_presigned_url"] == 1


def test_unsigned_s3_url_is_kept() -> None:
    url = "https://bucket.s3.amazonaws.com/key.png?versionId=abc123&X-Amz-Expires=60"
    assert sr.redact_text(url) == (url, Counter())


def test_netsuite_tba_secret_near_its_label() -> None:
    redacted, hits = sr.redact_text(f"NETSUITE_TOKEN_SECRET={HEX64}")
    assert_clean(redacted, HEX64)
    assert sum(hits.values()) == 1


def test_bare_hex_without_a_credential_label_is_kept() -> None:
    commit = HEX64
    redacted, hits = sr.redact_text(f"sha256:{commit}")
    assert redacted == f"sha256:{commit}"
    assert not hits


@pytest.mark.parametrize(
    "text",
    [
        'password = "hunter2hunter2"',
        "PASSWORD=hunter2hunter2",
        "client_secret: hunter2hunter2",
        '{"api_key": "hunter2hunter2"}',
        "password=Pa55.word99x",
    ],
)
def test_credential_assignments(text: str) -> None:
    redacted, hits = sr.redact_text(text)
    assert text.split("=")[-1].split(":")[-1].strip(' "}') not in redacted
    assert hits["credential_assignment"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "password=${DB_PASSWORD}",
        "api_key: <your-key-here>",
        "secret = {{ vault.secret }}",
        "password: changeme",
        "token_secret=xxxxxxxxxxxx",
        "the password field is required",
        "def check_password(value):",
        "Langfuse(secret_key=secret_key)",
        "client = Langfuse(secret_key=config.secret_key)",
        "api_key: settings.OPENAI_API_KEY",
    ],
)
def test_placeholders_and_prose_are_kept(text: str) -> None:
    redacted, hits = sr.redact_text(text)
    assert redacted == text
    assert not hits


def test_every_rule_has_triggers_it_can_match() -> None:
    for rule_id, _pattern, triggers in sr._RULES:
        assert triggers, rule_id
        assert all(t == t.lower() for t in triggers), rule_id


def test_ordinary_text_is_untouched() -> None:
    text = "Ran pytest: 1097 passed. See https://github.com/org/repo/pull/512 for details."
    assert sr.redact_text(text) == (text, Counter())


def test_json_attribute_stays_valid_json() -> None:
    payload = json.dumps({"role": "user", "content": f"key is {FAKES['github_token']}", "n": 1})
    redacted, hits = sr.redact_attribute_value(payload)
    parsed = json.loads(redacted)
    assert FAKES["github_token"] not in redacted
    assert parsed["n"] == 1
    assert hits["github_token"] == 1


def test_escaped_json_assignment_stays_valid_json() -> None:
    inner = 'export PASSWORD="hunter2hunter2"'
    payload = json.dumps({"command": inner})
    redacted, _ = sr.redact_attribute_value(payload)
    assert "hunter2hunter2" not in redacted
    json.loads(redacted)


def test_json_that_would_break_is_replaced_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"k": "v"})
    monkeypatch.setattr(sr, "redact_text", lambda text: ('{"k": broken', Counter(x=1)))
    redacted, hits = sr.redact_attribute_value(payload)
    assert redacted == sr.INVALID_JSON_MARKER
    assert hits["x"] == 1


def test_string_sequences_are_scanned_and_other_types_pass() -> None:
    values, hits = sr.redact_attribute_value(["safe", FAKES["npm_token"]])
    assert values[0] == "safe" and FAKES["npm_token"] not in values[1]
    assert hits["npm_token"] == 1
    for value in (42, 1.5, True, [1, 2]):
        assert sr.redact_attribute_value(value) == (value, Counter())


def test_redact_attributes_returns_only_changed_keys() -> None:
    changed, hits = sr.redact_attributes({"a": "plain", "b": f"x {FAKES['slack_token']}", "c": 3})
    assert set(changed) == {"b"}
    assert hits == Counter(slack_token=1)


def test_multiple_secrets_in_one_value() -> None:
    text = f"{FAKES['aws_access_key_id']} and {FAKES['github_token']} and {FAKES['aws_access_key_id']}"
    redacted, hits = sr.redact_text(text)
    assert hits == Counter(aws_access_key_id=2, github_token=1)
    for secret in FAKES.values():
        assert secret not in redacted


def test_large_attribute_is_scanned_quickly() -> None:
    import time

    filler = "lorem ipsum dolor sit amet, " * 200_000
    text = filler + FAKES["github_token"] + filler
    start = time.perf_counter()
    redacted, hits = sr.redact_text(text)
    assert time.perf_counter() - start < 5.0
    assert hits["github_token"] == 1


# ---- mask_otel_spans adapter ----


@dataclass(frozen=True)
class FakePatch:
    set_attributes: Mapping[str, Any] = field(default_factory=dict)
    delete_attributes: tuple = ()


@dataclass(frozen=True)
class FakeResult:
    span_patches: Mapping[Any, Optional[FakePatch]] = field(default_factory=dict)


@dataclass(frozen=True)
class FakeSpan:
    attributes: Mapping[str, Any]


@dataclass(frozen=True)
class FakeParams:
    spans: Mapping[str, FakeSpan]


def make_mask(**kwargs: Any) -> Any:
    return sr.make_mask_otel_spans(patch_type=FakePatch, result_type=FakeResult, **kwargs)


def test_mask_patches_only_spans_with_secrets() -> None:
    seen: list = []
    mask = make_mask(on_redaction=seen.append)
    params = FakeParams(spans={
        "clean": FakeSpan({"langfuse.observation.input": "hello"}),
        "dirty": FakeSpan({
            "langfuse.observation.output": f"token {FAKES['github_token']}",
            "langfuse.observation.input": "fine",
        }),
    })
    result = mask(params=params)
    assert set(result.span_patches) == {"dirty"}
    patch = result.span_patches["dirty"]
    assert FAKES["github_token"] not in patch.set_attributes["langfuse.observation.output"]
    assert "langfuse.observation.input" not in patch.set_attributes
    assert patch.set_attributes[sr.SUMMARY_ATTRIBUTE] == "github_token:1"
    assert seen == [Counter(github_token=1)]


def test_mask_fails_closed_when_redaction_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_attributes: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(sr, "redact_attributes", boom)
    mask = make_mask()
    result = mask(params=FakeParams(spans={"s": FakeSpan({"text": "anything", "n": 1, "tags": ["a"]})}))
    patch = result.span_patches["s"]
    assert patch.set_attributes["text"] == sr.FAILURE_MARKER
    assert patch.set_attributes["tags"] == [sr.FAILURE_MARKER]
    assert "n" not in patch.set_attributes
    assert patch.set_attributes[sr.SUMMARY_ATTRIBUTE] == "redaction_error:1"


def test_mask_survives_a_failing_callback() -> None:
    def explode(_hits: Counter) -> None:
        raise RuntimeError("logging failed")

    mask = make_mask(on_redaction=explode)
    result = mask(params=FakeParams(spans={"s": FakeSpan({"t": FAKES["npm_token"]})}))
    assert "s" in result.span_patches


def test_mask_uses_sdk_types_when_available() -> None:
    pytest.importorskip("langfuse.types", reason="real Langfuse SDK not installed")
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    if not hasattr(OtelSpanPatch, "__dataclass_fields__"):
        pytest.skip("langfuse is stubbed in this test session")
    mask = sr.make_mask_otel_spans()
    result = mask(params=FakeParams(spans={"s": FakeSpan({"t": FAKES["npm_token"]})}))
    assert isinstance(result, MaskOtelSpansResult)
    assert isinstance(result.span_patches["s"], OtelSpanPatch)
