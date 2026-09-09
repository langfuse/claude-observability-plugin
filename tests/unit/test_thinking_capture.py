from __future__ import annotations


def thinking_block(text: str, signature: str = "sig-abc") -> dict:
    return {"type": "thinking", "thinking": text, "signature": signature}


def assistant_row(content: list) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "id": "msg_1", "model": "claude-opus-5", "content": content},
        "timestamp": "2026-01-01T00:00:01.000Z",
    }


def generation_kwargs(hook_module, content: list) -> dict:
    kwargs, _ = hook_module.build_generation_kwargs(0, assistant_row(content), "do it", [], [])
    return kwargs


# ---- thinking parts ----

def test_thinking_blocks_become_chatml_thinking_parts(hook_module):
    kwargs = generation_kwargs(
        hook_module,
        [
            thinking_block("The user wants the config path, so I read the settings file."),
            {"type": "text", "text": "Reading the settings file."},
            {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "settings.json"}},
        ],
    )

    assert kwargs["output"] == {
        "role": "assistant",
        "content": "Reading the settings file.",
        "thinking": [
            {
                "type": "thinking",
                "content": "The user wants the config path, so I read the settings file.",
            }
        ],
        "tool_calls": [{"id": "toolu_read", "type": "function", "function": {"name": "Read"}}],
    }
    assert kwargs["metadata"]["thinking"] == [{"truncated": False, "orig_len": 60}]


def test_thinking_text_stays_out_of_assistant_content(hook_module):
    content = [thinking_block("secret reasoning"), {"type": "text", "text": "the answer"}]

    assert hook_module.extract_text_from_content(content) == "the answer"
    assert generation_kwargs(hook_module, content)["output"]["content"] == "the answer"


def test_interleaved_thinking_blocks_keep_transcript_order(hook_module):
    # Claude Code splits one assistant message across rows, so a merged
    # message can hold more than one thinking block.
    parts, metas = hook_module.build_thinking_parts(
        [
            thinking_block("first thought"),
            {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {}},
            thinking_block("second thought"),
        ]
    )

    assert [part["content"] for part in parts] == ["first thought", "second thought"]
    assert len(metas) == 2


def test_thinking_only_message_keeps_the_reasoning(hook_module):
    output = generation_kwargs(hook_module, [thinking_block("only reasoning")])["output"]

    assert output == {"role": "assistant", "thinking": [{"type": "thinking", "content": "only reasoning"}]}


# ---- blocks that hold no reasoning ----

def test_blocks_without_thinking_text_are_skipped(hook_module):
    # Claude Code writes the block with an empty text when the
    # showThinkingSummaries setting is off.
    kwargs = generation_kwargs(
        hook_module,
        [
            thinking_block(""),
            thinking_block("   \n  "),
            {"type": "thinking", "signature": "sig-only"},
            {"type": "thinking", "thinking": {"not": "a string"}},
            {"type": "text", "text": "the answer"},
        ],
    )

    assert kwargs["output"] == {"role": "assistant", "content": "the answer"}
    assert "thinking" not in kwargs["metadata"]


def test_thinking_parts_of_string_content_are_empty(hook_module):
    assert hook_module.build_thinking_parts("plain string content") == ([], [])
    assert hook_module.build_thinking_parts(None) == ([], [])


# ---- truncation ----

def test_long_thinking_is_truncated_and_recorded(hook_module):
    long_thinking = "t" * (hook_module.MAX_CHARS + 500)
    kwargs = generation_kwargs(hook_module, [thinking_block(long_thinking)])

    part = kwargs["output"]["thinking"][0]
    assert len(part["content"]) == hook_module.MAX_CHARS
    meta = kwargs["metadata"]["thinking"][0]
    assert meta["truncated"] is True
    assert meta["orig_len"] == len(long_thinking)
    assert meta["kept_len"] == hook_module.MAX_CHARS


# ---- signature ----

def test_thinking_signature_is_not_traced(hook_module):
    parts, _ = hook_module.build_thinking_parts([thinking_block("reasoning", signature="long-attestation-blob")])

    assert parts == [{"type": "thinking", "content": "reasoning"}]


def test_thinking_sits_beside_parallel_tool_calls(hook_module):
    kwargs = generation_kwargs(
        hook_module,
        [
            thinking_block("Two files to read, so two calls."),
            {"type": "tool_use", "id": "toolu_a", "name": "Read", "input": {"file_path": "a.py"}},
            {"type": "tool_use", "id": "toolu_b", "name": "Read", "input": {"file_path": "b.py"}},
        ],
    )

    assert kwargs["output"]["thinking"] == [
        {"type": "thinking", "content": "Two files to read, so two calls."}
    ]
    assert kwargs["output"]["tool_calls"] == [
        {"id": "toolu_a", "type": "function", "function": {"name": "Read"}},
        {"id": "toolu_b", "type": "function", "function": {"name": "Read"}},
    ]
    # A message of only thinking and tool_use extracts no text.
    assert "content" not in kwargs["output"]


def test_thinking_and_tool_calls_use_the_shared_shape_builder(hook_module):
    content = [
        thinking_block("Reading it."),
        {"type": "tool_use", "id": "toolu_read", "name": "Read", "input": {"file_path": "a.py"}},
    ]

    kwargs = generation_kwargs(hook_module, content)
    expected = hook_module.build_generation_output("", [{"id": "toolu_read", "name": "Read"}])

    assert kwargs["output"]["tool_calls"] == expected["tool_calls"]
