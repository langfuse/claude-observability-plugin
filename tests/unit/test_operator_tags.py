"""Pins the caller-supplied trace tag contract: both input shapes are accepted,
each tag is bounded, and bad input is ignored with a diagnostic."""

from typing import Any


def tags(hook_module: Any, raw: str) -> list:
    return hook_module.parse_operator_tags(raw)[0]


def warning(hook_module: Any, raw: str) -> str:
    return hook_module.parse_operator_tags(raw)[1]


def a_turn(hook_module: Any, read_fixture_jsonl: Any, fixture_transcript_path: Any) -> Any:
    return hook_module.build_turns(read_fixture_jsonl(fixture_transcript_path("simple_turn")))[0]


# ----------------- both input shapes -----------------

def test_a_comma_separated_list_becomes_tags(hook_module):
    assert tags(hook_module, "env:prod,team:platform") == ["env:prod", "team:platform"]


def test_a_json_array_becomes_tags(hook_module):
    assert tags(hook_module, '["env:prod", "lane:review"]') == ["env:prod", "lane:review"]


def test_json_array_entries_are_coerced_to_strings(hook_module):
    assert tags(hook_module, "[1, true]") == ["1", "True"]


def test_tags_keep_their_input_order(hook_module):
    assert tags(hook_module, "c,a,b") == ["c", "a", "b"]


def test_blank_and_duplicate_tags_are_dropped(hook_module):
    assert tags(hook_module, "a, ,a,  b  ") == ["a", "b"]


def test_a_tag_may_hold_spaces_and_colons(hook_module):
    assert tags(hook_module, "lane:review, two words") == ["lane:review", "two words"]


def test_no_tags_and_no_warning_for_empty_input(hook_module):
    assert hook_module.parse_operator_tags("") == ([], "")
    assert hook_module.parse_operator_tags("   ") == ([], "")


# ----------------- bad input is ignored, not fatal -----------------

def test_a_broken_json_array_is_ignored_with_a_diagnostic(hook_module):
    assert tags(hook_module, '["unclosed"') == []
    assert "not valid JSON" in warning(hook_module, '["unclosed"')


def test_a_json_object_is_ignored_with_a_diagnostic(hook_module):
    # Without the guard the object text becomes one tag, because it holds no
    # comma. The likely cause is a value meant for a different variable.
    raw = '{"lane": "review"}'

    assert tags(hook_module, raw) == []
    assert "not an array" in warning(hook_module, raw)


def test_an_over_long_tag_is_dropped_with_a_diagnostic(hook_module):
    raw = "keep," + "x" * (hook_module.MAX_OPERATOR_TAG_CHARS + 1)

    assert tags(hook_module, raw) == ["keep"]
    assert str(hook_module.MAX_OPERATOR_TAG_CHARS) in warning(hook_module, raw)


def test_tags_past_the_limit_are_dropped_with_a_diagnostic(hook_module):
    raw = ",".join(f"t{i}" for i in range(hook_module.MAX_OPERATOR_TAGS + 5))

    assert len(tags(hook_module, raw)) == hook_module.MAX_OPERATOR_TAGS
    assert "past the first" in warning(hook_module, raw)


def test_a_non_scalar_json_entry_is_dropped_with_a_diagnostic(hook_module):
    raw = '["keep", {"nested": 1}]'

    assert tags(hook_module, raw) == ["keep"]
    assert "non-scalar" in warning(hook_module, raw)


def test_one_diagnostic_names_a_reason_once(hook_module):
    raw = ",".join(["x" * 300] * 3)

    assert warning(hook_module, raw).count("characters") == 1


# ----------------- reaching the trace -----------------

def test_caller_tags_join_the_hook_tags(
    hook_module, monkeypatch, read_fixture_jsonl, fixture_transcript_path
):
    monkeypatch.setattr(hook_module, "OPERATOR_TAGS", ["env:prod", "lane:review"])
    turn = a_turn(hook_module, read_fixture_jsonl, fixture_transcript_path)

    assert hook_module.get_trace_tags(turn) == ["claude-code", "env:prod", "lane:review"]


def test_caller_tags_survive_with_skill_tags_turned_off(
    hook_module, monkeypatch, read_fixture_jsonl, fixture_transcript_path
):
    # The skill gate must not take the operator's own labels with it.
    monkeypatch.setattr(hook_module, "SKILL_TAGS", False)
    monkeypatch.setattr(hook_module, "OPERATOR_TAGS", ["lane:executor"])
    turn = a_turn(hook_module, read_fixture_jsonl, fixture_transcript_path)

    assert hook_module.get_trace_tags(turn) == ["claude-code", "lane:executor"]


def test_a_caller_tag_never_duplicates_a_hook_tag(
    hook_module, monkeypatch, read_fixture_jsonl, fixture_transcript_path
):
    monkeypatch.setattr(hook_module, "OPERATOR_TAGS", ["claude-code", "lane:background"])
    turn = a_turn(hook_module, read_fixture_jsonl, fixture_transcript_path)

    assert hook_module.get_trace_tags(turn) == ["claude-code", "lane:background"]


def test_no_caller_tags_leaves_the_hook_tags_untouched(
    hook_module, monkeypatch, read_fixture_jsonl, fixture_transcript_path
):
    monkeypatch.setattr(hook_module, "OPERATOR_TAGS", [])
    turn = a_turn(hook_module, read_fixture_jsonl, fixture_transcript_path)

    assert hook_module.get_trace_tags(turn) == ["claude-code"]


# ----------------- config precedence -----------------

def test_the_env_var_wins_over_the_wizard_option(hook_module, monkeypatch):
    monkeypatch.setenv("CC_LANGFUSE_TRACE_TAGS", "env:repo")
    monkeypatch.setenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TRACE_TAGS", "env:machine")

    assert hook_module._opt(hook_module.OPERATOR_TAGS_VAR) == "env:repo"


def test_a_bare_langfuse_trace_tags_variable_is_not_read(hook_module, monkeypatch):
    monkeypatch.delenv("CC_LANGFUSE_TRACE_TAGS", raising=False)
    monkeypatch.delenv("CLAUDE_PLUGIN_OPTION_CC_LANGFUSE_TRACE_TAGS", raising=False)
    monkeypatch.setenv("LANGFUSE_TRACE_TAGS", "should-be-ignored")

    assert hook_module._opt(hook_module.OPERATOR_TAGS_VAR) == ""
