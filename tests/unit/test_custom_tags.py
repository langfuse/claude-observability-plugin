from __future__ import annotations

import pytest


def parse(hook_module, raw: str):
    return hook_module.parse_custom_tags(raw)


def tags(hook_module, raw: str) -> list[str]:
    return parse(hook_module, raw)[0]


def warning(hook_module, raw: str) -> str:
    return parse(hook_module, raw)[1]


def test_empty_input_yields_no_tags(hook_module):
    assert parse(hook_module, "") == ([], "")


def test_single_tag(hook_module):
    assert tags(hook_module, "env:prod") == ["env:prod"]


def test_multiple_tags_keep_input_order(hook_module):
    assert tags(hook_module, "env:prod,team:platform") == ["env:prod", "team:platform"]


def test_surrounding_whitespace_is_stripped(hook_module):
    assert tags(hook_module, "  env:prod , team:platform  ") == [
        "env:prod",
        "team:platform",
    ]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("a,,b,", ["a", "b"]),
        (",", []),
        ("   ", []),
        (",,,", []),
    ],
)
def test_empty_tokens_are_dropped(hook_module, raw, expected):
    assert tags(hook_module, raw) == expected


def test_duplicates_collapse_onto_first_occurrence(hook_module):
    assert tags(hook_module, "b,a,b") == ["b", "a"]


def test_interior_whitespace_is_preserved(hook_module):
    """A tag is only split on commas, so a space inside one is not a separator."""
    assert tags(hook_module, "phase:review pass 2") == ["phase:review pass 2"]


def test_value_without_a_colon_is_a_valid_tag(hook_module):
    assert tags(hook_module, "nightly") == ["nightly"]


def test_tag_count_is_capped_and_warned(hook_module):
    raw = ",".join(f"tag{i}" for i in range(25))
    parsed, note = parse(hook_module, raw)

    assert len(parsed) == hook_module.MAX_CUSTOM_TAGS
    assert parsed[0] == "tag0"
    assert parsed[-1] == f"tag{hook_module.MAX_CUSTOM_TAGS - 1}"
    assert "CC_LANGFUSE_TAGS" in note
    assert "5" in note


def test_overlong_tag_is_dropped_not_truncated(hook_module):
    """A truncated tag is a *wrong* tag, and Langfuse tag facets are project-global."""
    overlong = "x" * (hook_module.MAX_CUSTOM_TAG_CHARS + 1)
    parsed, note = parse(hook_module, f"{overlong},env:prod")

    assert parsed == ["env:prod"]
    assert str(hook_module.MAX_CUSTOM_TAG_CHARS) in note


def test_tag_at_exactly_the_length_limit_is_kept(hook_module):
    exact = "x" * hook_module.MAX_CUSTOM_TAG_CHARS
    assert parse(hook_module, exact) == ([exact], "")


def test_no_warning_when_nothing_was_dropped(hook_module):
    assert warning(hook_module, "env:prod,team:platform") == ""


def test_pathological_input_returns_without_raising(hook_module):
    parsed, note = parse(hook_module, "x" * 100_000)

    assert parsed == []
    assert str(hook_module.MAX_CUSTOM_TAG_CHARS) in note


def test_many_pathological_tokens_stay_bounded(hook_module):
    parsed, _ = parse(hook_module, ",".join(f"t{i}" for i in range(10_000)))

    assert len(parsed) == hook_module.MAX_CUSTOM_TAGS
