"""Redact credentials from span attributes before they leave the machine.

Claude Code transcripts carry whatever the session printed: tokens pasted into
a prompt, `.env` files a tool read, connection strings in command output. The
hook exports that text as span attributes, so this module scrubs every string
attribute at the SDK's export stage (`Langfuse(mask_otel_spans=...)`), after
the hook has built the spans and before the batch is sent.

Design rules:

- Standard library only. The hook is a single uv script with no package, and
  this module sits next to it.
- Cheap before exact. Every rule lists literal triggers that any match must
  contain; its regular expression runs only when a trigger appears in the
  lower-cased text. A multi-megabyte attribute is mostly handled by substring
  searches, which matters because the mask runs inside the hook's flush window.
- Rules run in order, so a more specific rule claims text before a broader one.
- Replacements contain no quotes or backslashes, so a JSON-encoded attribute
  stays parseable. If a replacement would still break valid JSON, the whole
  attribute is replaced instead.
- Fail closed. If redacting a span raises, every string attribute of that span
  is replaced with a marker rather than exported as is. The returned mask
  function never raises, because a raising mask makes the SDK drop the whole
  batch.

Detection is pattern-based and favours known credential formats. It is a
safety net for accidental exposure, not a guarantee that no secret is ever
exported.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

REDACTION_MARKER = "[REDACTED:{rule}]"
FAILURE_MARKER = "[REDACTED:redaction_error]"
INVALID_JSON_MARKER = "[REDACTED:unparseable_after_redaction]"

# Attribute that records which rules fired on a span. The `langfuse.observation.
# metadata.` prefix makes it show up as observation metadata in Langfuse.
SUMMARY_ATTRIBUTE = "langfuse.observation.metadata.secret_redactions"

# Credential-shaped key names whose assigned value is redacted, e.g.
# `password=...`, `"client_secret": "..."`, `TOKEN_SECRET: ...`.
_SECRET_KEY_NAMES = (
    r"pass(?:word|wd|phrase)?|pwd|secret|client[_-]?secret|consumer[_-]?secret"
    r"|token[_-]?secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token"
    r"|refresh[_-]?token|private[_-]?key|secret[_-]?key|session[_-]?token"
)

# Values that look like an assignment but are placeholders or references, not
# secrets. Checked against the captured value only.
_PLACEHOLDER_VALUE = re.compile(
    r"^(?:[$<{%*]|\[REDACTED|x{6,}$|null$|none$|true$|false$|undefined$|redacted$|changeme$)",
    re.IGNORECASE,
)

# Values that are code, not secrets: an identifier or attribute path that itself
# names a credential, as in `secret_key=secret_key` or `api_key=settings.API_KEY`.
# Matched whole, case-insensitively.
_REFERENCE_VALUE = re.compile(
    r"(?:[a-z_][a-z0-9_]*\.)*[a-z_]*(?:" + _SECRET_KEY_NAMES + r")[a-z0-9_]*",
    re.IGNORECASE,
)

# (rule id, pattern, triggers). A pattern with a group named `v` redacts only
# that group; otherwise the whole match is redacted. Triggers are lower-case
# substrings, at least one of which every match contains; they must stay a
# superset of what the pattern can match, or the rule silently stops firing.
_RULES: Tuple[Tuple[str, str, Tuple[str, ...]], ...] = (
    (
        "private_key",
        r"-----BEGIN[A-Z0-9 ]*PRIVATE KEY-----[^\"]{0,8000}?(?:-----END[A-Z0-9 ]*PRIVATE KEY-----|(?=\")|$)",
        ("private key",),
    ),
    (
        # SigV4 presigned URLs (query string, also JSON-escaped `&`) and the
        # `x-amz-security-token` header. The session token grants the signer's
        # role until it expires, so the URL is a live credential.
        "aws_presigned_url",
        r"(?i:(?:[?&]|\\u0026)X-Amz-(?:Security-Token|Signature|Credential)="
        r"|\bx-amz-security-token\\?[\"']?\s*:\s*\\?[\"']?)"
        r"(?P<v>[^&\s\"'\\<>]{8,})",
        ("x-amz-",),
    ),
    ("aws_access_key_id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b", ("akia", "asia", "abia", "acca")),
    (
        "aws_secret_access_key",
        r"(?i:aws_?secret_?access_?key|aws_?secret_?key|secret_?access_?key)"
        r"\\?[\"']?\s*[:=]\s*\\?[\"']?(?P<v>[A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])",
        ("secret",),
    ),
    ("github_token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,255}|github_pat_[A-Za-z0-9_]{60,255})\b", ("ghp_", "gho_", "ghu_", "ghs_", "ghr_", "github_pat_")),
    ("gitlab_token", r"\bglpat-[A-Za-z0-9_-]{20,}\b", ("glpat-",)),
    ("slack_token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}", ("xox",)),
    ("slack_webhook", r"https://hooks\.slack\.com/services/[A-Za-z0-9_/]{20,}", ("hooks.slack.com",)),
    ("langfuse_secret_key", r"\bsk-lf-[0-9a-fA-F-]{36}\b", ("sk-lf-",)),
    ("anthropic_api_key", r"\bsk-ant-[A-Za-z0-9_-]{20,}", ("sk-ant-",)),
    ("openai_api_key", r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,}", ("sk-",)),
    ("google_api_key", r"\bAIza[0-9A-Za-z_-]{35}\b", ("aiza",)),
    ("stripe_key", r"\b(?:sk|rk)_(?:live|test)_[0-9A-Za-z]{24,}\b", ("_live_", "_test_")),
    ("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b", ("npm_",)),
    ("pypi_token", r"\bpypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}", ("pypi-",)),
    ("atlassian_api_token", r"\bATATT3[A-Za-z0-9_=-]{30,}", ("atatt3",)),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", ("eyj",)),
    (
        "url_credentials",
        r"(?<=://)[^/\s:@\"'\\]+:(?P<v>[^@\s/\"'\\]{3,})(?=@)",
        ("://",),
    ),
    ("bearer_token", r"(?i:\bbearer)\s+(?P<v>[A-Za-z0-9._~+/=-]{20,})", ("bearer",)),
    ("basic_auth", r"(?i:\bbasic)\s+(?P<v>[A-Za-z0-9+/]{16,}={0,2})(?![A-Za-z0-9+/=])", ("basic",)),
    (
        "netsuite_tba_secret",
        r"(?i:netsuite|token[_ -]?id|token[_ -]?secret|consumer[_ -]?key|consumer[_ -]?secret)"
        r"[^\"\n]{0,40}?\b(?P<v>[0-9a-fA-F]{64})\b",
        ("netsuite", "token", "consumer"),
    ),
    (
        "credential_assignment",
        r"(?i:\b(?:" + _SECRET_KEY_NAMES + r"))\\?[\"']?\s*[:=]\s*\\?[\"']?"
        r"(?P<v>[^\s\"'\\,;)}\]]{8,})",
        ("pass", "pwd", "secret", "key", "token"),
    ),
)


_COMPILED: Tuple[Tuple[str, "re.Pattern[str]", Tuple[str, ...]], ...] = tuple(
    (rule_id, re.compile(pattern), triggers) for rule_id, pattern, triggers in _RULES
)


def _replacer(rule_id: str, hits: Counter) -> Callable[["re.Match[str]"], str]:
    marker = REDACTION_MARKER.format(rule=rule_id)

    def replace(match: "re.Match[str]") -> str:
        if "v" in match.re.groupindex and match.group("v") is not None:
            value = match.group("v")
            if _PLACEHOLDER_VALUE.match(value) or _REFERENCE_VALUE.fullmatch(value):
                return match.group(0)
            hits[rule_id] += 1
            start = match.start("v") - match.start(0)
            end = match.end("v") - match.start(0)
            whole = match.group(0)
            return whole[:start] + marker + whole[end:]
        hits[rule_id] += 1
        return marker

    return replace


def redact_text(text: str) -> Tuple[str, Counter]:
    """Return `text` with credentials replaced, and a count of hits per rule."""
    hits: Counter = Counter()
    # Redaction only removes text, so triggers checked against the original
    # can only over-report, never miss.
    folded = text.lower()
    for rule_id, pattern, triggers in _COMPILED:
        if any(trigger in folded for trigger in triggers):
            text = pattern.sub(_replacer(rule_id, hits), text)
    return text, hits


def _is_json(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped or stripped[0] not in "{[\"":
        return False
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def redact_attribute_value(value: Any) -> Tuple[Any, Counter]:
    """Redact a single OpenTelemetry attribute value.

    Strings and sequences of strings are scanned; other types pass through.
    A string that was valid JSON stays valid JSON, or is replaced whole.
    """
    if isinstance(value, str):
        redacted, hits = redact_text(value)
        if hits and redacted != value and _is_json(value) and not _is_json(redacted):
            return INVALID_JSON_MARKER, hits
        return redacted, hits
    if isinstance(value, (list, tuple)) and value and all(isinstance(item, str) for item in value):
        total: Counter = Counter()
        items = []
        for item in value:
            redacted_item, hits = redact_attribute_value(item)
            items.append(redacted_item)
            total.update(hits)
        return (tuple(items) if isinstance(value, tuple) else items), total
    return value, Counter()


def redact_attributes(attributes: Mapping[str, Any]) -> Tuple[Dict[str, Any], Counter]:
    """Return only the attributes that changed, with their redacted values."""
    changed: Dict[str, Any] = {}
    total: Counter = Counter()
    for key, value in attributes.items():
        redacted, hits = redact_attribute_value(value)
        if hits:
            changed[key] = redacted
            total.update(hits)
    return changed, total


def _failure_patch_values(attributes: Mapping[str, Any]) -> Dict[str, Any]:
    """Blank every string attribute: used when redaction itself fails."""
    blanked: Dict[str, Any] = {}
    for key, value in attributes.items():
        if isinstance(value, str):
            blanked[key] = FAILURE_MARKER
        elif isinstance(value, (list, tuple)) and any(isinstance(item, str) for item in value):
            blanked[key] = [FAILURE_MARKER]
    return blanked


def summarize(hits: Counter) -> str:
    """Stable, human-readable summary such as `aws_access_key_id:1,jwt:2`."""
    return ",".join(f"{rule}:{count}" for rule, count in sorted(hits.items()))


def make_mask_otel_spans(
    patch_type: Optional[Callable[..., Any]] = None,
    result_type: Optional[Callable[..., Any]] = None,
    on_redaction: Optional[Callable[[Counter], None]] = None,
) -> Callable[..., Any]:
    """Build a `mask_otel_spans` function for `Langfuse(mask_otel_spans=...)`.

    `patch_type` and `result_type` default to the SDK's `OtelSpanPatch` and
    `MaskOtelSpansResult`; tests pass stand-ins. `on_redaction` receives the
    per-batch hit counts, for logging counts (never values).
    """
    if patch_type is None or result_type is None:
        from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

        patch_type = patch_type or OtelSpanPatch
        result_type = result_type or MaskOtelSpansResult

    def mask_otel_spans(*, params: Any) -> Any:
        patches: Dict[Any, Any] = {}
        batch_hits: Counter = Counter()
        for identifier, span in params.spans.items():
            attributes = getattr(span, "attributes", {}) or {}
            try:
                changed, hits = redact_attributes(attributes)
                if not hits:
                    continue
                changed[SUMMARY_ATTRIBUTE] = summarize(hits)
                batch_hits.update(hits)
            except Exception:
                changed = _failure_patch_values(attributes)
                changed[SUMMARY_ATTRIBUTE] = "redaction_error:1"
                batch_hits["redaction_error"] += 1
            patches[identifier] = patch_type(set_attributes=changed)
        if batch_hits and on_redaction is not None:
            try:
                on_redaction(batch_hits)
            except Exception:
                pass
        return result_type(span_patches=patches)

    return mask_otel_spans
