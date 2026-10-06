from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any


def _entry(hours_ago: float, open_turn: dict) -> dict:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {"offset": 10, "turn_count": 3, "open_turn": open_turn, "updated": ts.isoformat()}


HELD = {"user_row_uuid": "u1", "rows": [{"uuid": "u1", "type": "user"}]}


def test_idle_session_open_turn_is_dropped(hook_module: Any) -> None:
    state = {"idle": _entry(48, dict(HELD)), "active": _entry(1, dict(HELD))}
    hook_module.save_hook_state(state)
    saved = json.loads(hook_module.STATE_FILE.read_text(encoding="utf-8"))
    assert saved["idle"]["open_turn"] == {}
    # Cursor survives so a resumed session does not re-emit its turns.
    assert saved["idle"]["offset"] == 10 and saved["idle"]["turn_count"] == 3
    assert saved["active"]["open_turn"] == HELD


def test_open_turn_ttl_is_configurable(hook_module: Any, monkeypatch: Any) -> None:
    monkeypatch.setattr(hook_module, "OPEN_TURN_TTL_HOURS", 0.5)
    state = {"s": _entry(1, dict(HELD))}
    hook_module.save_hook_state(state)
    saved = json.loads(hook_module.STATE_FILE.read_text(encoding="utf-8"))
    assert saved["s"]["open_turn"] == {}


def test_thirty_day_eviction_still_applies(hook_module: Any) -> None:
    state = {"old": _entry(31 * 24, dict(HELD)), "new": _entry(1, {})}
    hook_module.save_hook_state(state)
    saved = json.loads(hook_module.STATE_FILE.read_text(encoding="utf-8"))
    assert set(saved) == {"new"}


def test_state_file_is_written_compact(hook_module: Any) -> None:
    hook_module.save_hook_state({"s": _entry(1, dict(HELD))})
    text = hook_module.STATE_FILE.read_text(encoding="utf-8")
    assert "\n" not in text and ": " not in text
