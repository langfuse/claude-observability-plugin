from __future__ import annotations

import hashlib


def expected_trace_id_int(session_id: str, user_row_uuid: str) -> int:
    seed = f"{session_id}:{user_row_uuid}"
    return int(hashlib.sha256(seed.encode("utf-8")).digest()[:16].hex(), 16)


def test_remote_parent_pins_deterministic_trace_id(hook_module, fake_langfuse):
    parent = hook_module.remote_parent(fake_langfuse, "sess-1", "row-uuid-1")

    context = parent.get_span_context()
    assert context.trace_id == expected_trace_id_int("sess-1", "row-uuid-1")
    assert context.span_id != 0


def test_remote_parent_without_root_span_id_varies_phantom_span_id(hook_module, fake_langfuse):
    contexts = [
        hook_module.remote_parent(fake_langfuse, "sess-1", "row-uuid-1").get_span_context()
        for _ in range(2)
    ]
    # Same turn, same trace id -- but the phantom span id is random because
    # children must become trace roots, not siblings under a stable parent.
    assert contexts[0].trace_id == contexts[1].trace_id


def test_remote_parent_nests_under_stored_root_span_id(hook_module, fake_langfuse):
    root_span_id = "00f067aa0ba902b7"

    parent = hook_module.remote_parent(
        fake_langfuse, "sess-1", "row-uuid-1", root_span_id=root_span_id
    )

    context = parent.get_span_context()
    assert context.span_id == int(root_span_id, 16)
    assert context.trace_id == expected_trace_id_int("sess-1", "row-uuid-1")


def test_remote_parent_requires_user_row_uuid(hook_module, fake_langfuse):
    assert hook_module.remote_parent(fake_langfuse, "sess-1", None) is None
    assert hook_module.remote_parent(fake_langfuse, "sess-1", "") is None
