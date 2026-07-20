from __future__ import annotations

from datetime import datetime, timezone


def test_to_otel_nanoseconds_keeps_the_exact_millisecond(hook_module):
    # Regression: int(ts.timestamp() * 1e9) truncates values that float math
    # places just below the millisecond (…58599999… -> .585 instead of .586);
    # observed live as a systematic -1ms drift on emitted span times.
    ts = datetime(2026, 7, 20, 17, 6, 8, 586000, tzinfo=timezone.utc)

    ns = hook_module.to_otel_nanoseconds(ts)

    assert ns % 1_000_000 == 0
    assert (ns // 1_000_000) % 1000 == 586
