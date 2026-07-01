#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "langfuse>=4.0,<5",
# ]
# ///
"""
Claude Code -> Langfuse hook

"""

import json
import logging
import os
import sys
import threading
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ----------------- Langfuse import (fail-open) -----------------
try:
    from langfuse import Langfuse, propagate_attributes
    from opentelemetry import trace as otel_trace_api
except Exception:
    sys.exit(0)

# ----------------- Paths -----------------
STATE_DIR = Path.home() / ".claude" / "state"
LOG_FILE = STATE_DIR / "langfuse_hook.log"
STATE_FILE = STATE_DIR / "langfuse_state.json"
LOCK_FILE = STATE_DIR / "langfuse_state.lock"

# ----------------- Configuration -----------------
def _opt(name: str) -> str:
    """Read a plugin userConfig value (CLAUDE_PLUGIN_OPTION_<NAME>) with a fallback to a plain env var."""
    return os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name}") or os.environ.get(name) or ""

DEBUG = _opt("CC_LANGFUSE_DEBUG").lower() == "true"
SKILL_TAGS = (_opt("CC_LANGFUSE_SKILL_TAGS") or "true").lower() == "true"
CAPTURE_SKILL_CONTENT = _opt("CC_LANGFUSE_CAPTURE_SKILL_CONTENT").lower() == "true"
try:
    MAX_CHARS = int(_opt("CC_LANGFUSE_MAX_CHARS") or "20000")
except ValueError:
    MAX_CHARS = 20000


@dataclass
class LangfuseConfig:
    public_key: str
    secret_key: str
    host: str
    user_id: Optional[str]


def get_langfuse_config() -> Optional[LangfuseConfig]:
    public_key = _opt("LANGFUSE_PUBLIC_KEY") or _opt("CC_LANGFUSE_PUBLIC_KEY")
    secret_key = _opt("LANGFUSE_SECRET_KEY") or _opt("CC_LANGFUSE_SECRET_KEY")
    host = _opt("LANGFUSE_BASE_URL") or _opt("CC_LANGFUSE_BASE_URL") or "https://us.cloud.langfuse.com"
    user_id = _opt("LANGFUSE_USER_ID") or _opt("CC_LANGFUSE_USER_ID") or None

    if not public_key or not secret_key:
        return None

    return LangfuseConfig(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
        user_id=user_id,
    )


def create_langfuse_client(config: LangfuseConfig) -> Optional[Langfuse]:
    try:
        return Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            host=config.host,
        )
    except Exception:
        return None


# ----------------- Logging -----------------
_logger: Optional[logging.Logger] = None

def _get_logger() -> Optional[logging.Logger]:
    global _logger
    if _logger is not None:
        return _logger
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        lg = logging.getLogger("langfuse_hook")
        lg.setLevel(logging.DEBUG if DEBUG else logging.INFO)
        if not lg.handlers:
            h = RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3)
            h.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            lg.addHandler(h)
        _logger = lg
        return _logger
    except Exception:
        return None

def debug(msg: str) -> None:
    if not DEBUG:
        return
    lg = _get_logger()
    if lg is not None:
        try:
            lg.debug(msg)
        except Exception:
            pass

def info(msg: str) -> None:
    lg = _get_logger()
    if lg is not None:
        try:
            lg.info(msg)
        except Exception:
            pass

# ----------------- State locking (best-effort) -----------------
class FileLock:
    def __init__(self, path: Path, timeout_s: float = 2.0):
        self.path = path
        self.timeout_s = timeout_s
        self._fh = None

    def __enter__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        self.acquired = False
        try:
            import fcntl  # Unix only
        except ImportError:
            # No fcntl available (e.g. Windows) — proceed without lock.
            return self
        deadline = time.time() + self.timeout_s
        try:
            while True:
                try:
                    fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.acquired = True
                    return self
                except BlockingIOError:
                    if time.time() > deadline:
                        raise TimeoutError(
                            f"could not acquire {self.path} within {self.timeout_s}s"
                        )
                    time.sleep(0.05)
        except BaseException:
            # __exit__ is not called when __enter__ raises — close the fh
            # we just opened so it doesn't leak.
            try:
                self._fh.close()
            except Exception:
                pass
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            import fcntl
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass

def load_state() -> Dict[str, Any]:
    try:
        if not STATE_FILE.exists():
            return {}
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}

def save_state(state: Dict[str, Any]) -> None:
    try:
        # Drop session entries older than 30 days to keep the file bounded.
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        for k in list(state.keys()):
            entry = state.get(k)
            if not isinstance(entry, dict):
                continue
            updated = entry.get("updated")
            if not isinstance(updated, str):
                continue
            try:
                ts = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            except Exception:
                continue
            if ts < cutoff:
                del state[k]
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        debug(f"save_state failed: {e}")

def state_key(session_id: str, transcript_path: str) -> str:
    # stable key even if session_id collides
    raw = f"{session_id}::{transcript_path}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

# ----------------- Hook payload -----------------
def read_hook_payload() -> Dict[str, Any]:
    """
    Claude Code hooks pass a JSON payload on stdin.
    This script tolerates missing/empty stdin by returning {}.
    """
    try:
        data = sys.stdin.read()
        debug(f"stdin received {len(data)} chars")
        if not data.strip():
            return {}
        parsed = json.loads(data)
        if isinstance(parsed, dict):
            debug(f"payload top-level keys: {sorted(parsed.keys())}")
        return parsed
    except Exception as e:
        debug(f"read_hook_payload exception: {e!r}")
        return {}

def extract_session_id_and_transcript_path(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[Path]]:
    """
    Tries a few plausible field names; exact keys can vary across hook types/versions.
    Prefer structured values from stdin over heuristics.
    """
    session_id = (
        payload.get("sessionId")
        or payload.get("session_id")
        or payload.get("session", {}).get("id")
    )

    transcript_path_raw = (
        payload.get("transcriptPath")
        or payload.get("transcript_path")
        or payload.get("transcript", {}).get("path")
    )

    if transcript_path_raw:
        try:
            transcript_path = Path(transcript_path_raw).expanduser().resolve()
        except Exception:
            transcript_path = None
    else:
        transcript_path = None

    return session_id, transcript_path

# ----------------- Transcript parsing helpers -----------------
def get_content_from_row(row: Dict[str, Any]) -> Any:
    if not isinstance(row, dict):
        return None
    message = row.get("message")
    if isinstance(message, dict):
        return message.get("content")
    return row.get("content")

def get_user_or_assistant_role_from_row(row: Dict[str, Any]) -> Optional[str]:
    # Claude Code transcript row format is internal. Prefer top-level row.type
    # when it marks a chat row, then fall back to nested message.role.
    row_type = row.get("type")
    if row_type in ("user", "assistant"):
        return row_type

    message = row.get("message")
    if isinstance(message, dict):
        role = message.get("role")
        if role in ("user", "assistant"):
            return role
    return None

def is_tool_result(row: Dict[str, Any]) -> bool:
    role = get_user_or_assistant_role_from_row(row)
    if role != "user":
        return False
    content = get_content_from_row(row)
    if isinstance(content, list):
        return any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content)
    return False

def get_tool_result_blocks(content: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for x in content:
            if isinstance(x, dict) and x.get("type") == "tool_result":
                out.append(x)
    return out

def get_tool_use_blocks(content: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(content, list):
        for x in content:
            if isinstance(x, dict) and x.get("type") == "tool_use":
                out.append(x)
    return out

def extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for x in content:
            if isinstance(x, dict) and x.get("type") == "text":
                parts.append(x.get("text", ""))
            elif isinstance(x, str):
                parts.append(x)
        return "\n".join([p for p in parts if p])
    return ""

def truncate_text(s: str, max_chars: int = MAX_CHARS) -> Tuple[str, Dict[str, Any]]:
    if s is None:
        return "", {"truncated": False, "orig_len": 0}
    orig_len = len(s)
    if orig_len <= max_chars:
        return s, {"truncated": False, "orig_len": orig_len}
    head = s[:max_chars]
    return head, {"truncated": True, "orig_len": orig_len, "kept_len": len(head), "sha256": hashlib.sha256(s.encode("utf-8")).hexdigest()}

def get_model(msg: Dict[str, Any]) -> str:
    m = msg.get("message")
    if isinstance(m, dict):
        return m.get("model") or "claude"
    return "claude"

def get_usage_details_from_row(row: Dict[str, Any]) -> Optional[Dict[str, int]]:
    """Extract Anthropic token usage from an assistant message, if present."""
    m = row.get("message")
    if not isinstance(m, dict):
        return None
    u = m.get("usage")
    if not isinstance(u, dict):
        return None
    details: Dict[str, int] = {}
    for src, dst in (
        ("input_tokens", "input"),
        ("output_tokens", "output"),
        ("cache_read_input_tokens", "cache_read_input_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
    ):
        v = u.get(src)
        if isinstance(v, int) and v > 0:
            details[dst] = v
    return details or None

def get_message_id(msg: Dict[str, Any]) -> Optional[str]:
    m = msg.get("message")
    if isinstance(m, dict):
        mid = m.get("id")
        if isinstance(mid, str) and mid:
            return mid
    return None

def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse a Claude Code jsonl row timestamp (ISO 8601 with trailing Z)."""
    if isinstance(value, dict):
        value = value.get("timestamp")
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None

# ----------------- Incremental reader -----------------
@dataclass
class SessionState:
    offset: int = 0       # Last byte read from the transcript file.
    buffer: str = ""      # Partial JSONL line kept between hook runs.
    turn_count: int = 0   # Turns already emitted for this session.
    pending_agent_turns: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)

def load_session_state(global_state: Dict[str, Any], key: str) -> SessionState:
    s = global_state.get(key, {})
    pending = s.get("pending_agent_turns")
    if not isinstance(pending, dict):
        pending = {}
    return SessionState(
        offset=int(s.get("offset", 0)),
        buffer=str(s.get("buffer", "")),
        turn_count=int(s.get("turn_count", 0)),
        pending_agent_turns=pending,
    )

def write_session_state(global_state: Dict[str, Any], key: str, ss: SessionState) -> None:
    global_state[key] = {
        "offset": ss.offset,
        "buffer": ss.buffer,
        "turn_count": ss.turn_count,
        "pending_agent_turns": ss.pending_agent_turns or {},
        "updated": datetime.now(timezone.utc).isoformat(),
    }

def read_new_jsonl(transcript_path: Path, ss: SessionState) -> Tuple[List[Dict[str, Any]], SessionState]:
    """
    Reads only new bytes since ss.offset. Keeps ss.buffer for partial last line.
    Returns parsed JSON lines (best-effort) and updated state.
    """
    if not transcript_path.exists():
        return [], ss

    try:
        file_size = transcript_path.stat().st_size
        if file_size < ss.offset:
            # Transcript was rotated or truncated — restart from the beginning.
            debug(f"transcript shrank ({file_size} < {ss.offset}); restarting")
            ss.offset = 0
            ss.buffer = ""
        with open(transcript_path, "rb") as f:
            f.seek(ss.offset)
            chunk = f.read()
            new_offset = f.tell()
    except Exception as e:
        debug(f"read_new_jsonl failed: {e}")
        return [], ss

    if not chunk:
        return [], ss

    try:
        text = chunk.decode("utf-8", errors="replace")
    except Exception:
        text = chunk.decode(errors="replace")

    combined = ss.buffer + text
    lines = combined.split("\n")
    # last element may be incomplete
    ss.buffer = lines[-1]
    ss.offset = new_offset

    msgs: List[Dict[str, Any]] = []
    for line in lines[:-1]:
        line = line.strip()
        if not line:
            continue
        try:
            msgs.append(json.loads(line))
        except Exception:
            continue

    return msgs, ss

# ----------------- Turn assembly -----------------
@dataclass
class Turn:
    user_msg: Dict[str, Any]
    assistant_msgs: List[Dict[str, Any]]
    tool_results_by_id: Dict[str, Any]
    tool_use_timestamps_by_id: Dict[str, Any]
    # Injected context (e.g. skill instructions) keyed by the tool_use id it
    # belongs to, taken from isMeta rows carrying sourceToolUseID.
    injected_by_tool_id: Dict[str, str]
    rows: List[Dict[str, Any]]

def _extract_xml_tag_value(text: str, tag: str) -> Optional[str]:
    start = f"<{tag}>"
    end = f"</{tag}>"
    i = text.find(start)
    if i < 0:
        return None
    j = text.find(end, i + len(start))
    if j < 0:
        return None
    return text[i + len(start):j]

def get_tool_use_id_from_task_notification(row: Dict[str, Any]) -> Optional[str]:
    notification_text = extract_text(get_content_from_row(row)).lstrip()
    if not notification_text.startswith("<task-notification>"):
        return None
    tool_use_id = _extract_xml_tag_value(notification_text, "tool-use-id")
    return tool_use_id.strip() if isinstance(tool_use_id, str) and tool_use_id.strip() else None

def get_result_from_task_notification_row(row: Dict[str, Any]) -> str:
    notification_text = extract_text(get_content_from_row(row))
    result = _extract_xml_tag_value(notification_text, "result")
    return result if result is not None else notification_text

def get_pending_agent_tool_use_ids(turn: Turn) -> List[str]:
    tool_use_ids: List[str] = []
    for assistant_message in turn.assistant_msgs:
        for tool_use_block in get_tool_use_blocks(get_content_from_row(assistant_message)):
            if tool_use_block.get("name") not in ("Agent", "Task"):
                continue
            tool_use_id = str(tool_use_block.get("id") or "")
            if not tool_use_id:
                continue
            tool_result_entry = turn.tool_results_by_id.get(tool_use_id)
            if isinstance(tool_result_entry, dict) and tool_result_entry.get("final_content") is not None:
                continue
            tool_result_content = tool_result_entry.get("content") if isinstance(tool_result_entry, dict) else None
            tool_result_text = tool_result_content if isinstance(tool_result_content, str) else json.dumps(tool_result_content, ensure_ascii=False)
            if "Async agent launched successfully" in tool_result_text:
                tool_use_ids.append(tool_use_id)
    return tool_use_ids

def prepend_deferred_agent_turn_rows(messages: List[Dict[str, Any]], ss: SessionState) -> List[Dict[str, Any]]:
    if not ss.pending_agent_turns:
        return messages
    rows: List[Dict[str, Any]] = []
    for row in messages:
        tool_use_id = get_tool_use_id_from_task_notification(row)
        if tool_use_id and tool_use_id in ss.pending_agent_turns:
            rows.extend(ss.pending_agent_turns.pop(tool_use_id))
        rows.append(row)
    return rows

def get_subagent_transcripts_by_tool_use_id(transcript_path: Path) -> Dict[str, Dict[str, Any]]:
    """Map launching Agent/Task tool_use ids to their subagent transcripts."""
    subagent_dir = transcript_path.with_suffix("") / "subagents"
    if not subagent_dir.is_dir():
        return {}

    subagent_transcripts_by_tool_use_id: Dict[str, Dict[str, Any]] = {}
    for meta_path in subagent_dir.glob("*.meta.json"):
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue

        tool_use_id = metadata.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id:
            continue

        jsonl_path = meta_path.with_name(meta_path.name[: -len(".meta.json")] + ".jsonl")
        if not jsonl_path.exists():
            continue

        subagent_transcripts_by_tool_use_id[tool_use_id] = {
            "path": jsonl_path,
            "agent_type": metadata.get("agentType"),
            "description": metadata.get("description"),
        }
    return subagent_transcripts_by_tool_use_id

def merge_assistant_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Claude Code can split one assistant message across multiple JSONL rows that
    share message.id. Merge them back into one logical message by concatenating
    content blocks in row order.
    """
    base: Dict[str, Any] = dict(rows[-1])
    last_message = rows[-1].get("message")
    merged_message: Dict[str, Any] = dict(last_message) if isinstance(last_message, dict) else {}

    merged_content: List[Any] = []
    for row in rows:
        message_obj = row.get("message")
        if not isinstance(message_obj, dict):
            continue

        content_blocks = message_obj.get("content")
        if isinstance(content_blocks, list):
            merged_content.extend(content_blocks)
        elif isinstance(content_blocks, str) and content_blocks:
            merged_content.append({"type": "text", "text": content_blocks})

    merged_message["content"] = merged_content
    base["message"] = merged_message
    return base


def build_turns(messages: List[Dict[str, Any]]) -> List[Turn]:
    """
    Groups incremental transcript rows into turns:
    user (non-tool-result) -> assistant messages -> (tool_result rows, possibly interleaved)
    Uses:
    - assistant rows merged by message.id (all content blocks concatenated)
    - tool results dedupe by tool_use_id (latest wins)
    """
    turns: List[Turn] = []
    current_turn_user_row: Optional[Dict[str, Any]] = None

    # assistant messages for current turn:
    assistant_message_ids: List[str] = []             # message ids in order of first appearance (or synthetic)
    assistant_rows_by_message_id: Dict[str, List[Dict[str, Any]]] = {}  # id -> all rows (merged at flush)

    tool_results_by_id: Dict[str, Any] = {}     # tool_use_id -> content
    tool_use_timestamps_by_id: Dict[str, Any] = {}  # tool_use_id -> row timestamp
    injected_by_tool_id: Dict[str, str] = {}    # tool_use_id -> injected text (skill instructions)
    current_rows: List[Dict[str, Any]] = []

    def flush_turn():
        nonlocal current_turn_user_row, assistant_message_ids, assistant_rows_by_message_id, tool_results_by_id, tool_use_timestamps_by_id, injected_by_tool_id, current_rows, turns
        if current_turn_user_row is None:
            return
        if not assistant_rows_by_message_id:
            return
        # Rebuild one assistant message per message.id, in the order the ids
        # first appeared. assistant_rows_by_message_id[message_id] holds all raw rows that shared that
        # id; merge_assistant_rows concatenates their content blocks into one.
        merged_assistant_rows: List[Dict[str, Any]] = []
        for message_id in assistant_message_ids:
            rows_for_id = assistant_rows_by_message_id.get(message_id)
            if not rows_for_id:
                continue
            merged_assistant_rows.append(merge_assistant_rows(rows_for_id))
        turns.append(Turn(
            user_msg=current_turn_user_row,
            assistant_msgs=merged_assistant_rows,
            tool_results_by_id=dict(tool_results_by_id),
            tool_use_timestamps_by_id=dict(tool_use_timestamps_by_id),
            injected_by_tool_id=dict(injected_by_tool_id),
            rows=list(current_rows),
        ))

    for row in messages:
        # Injected user rows (slash-command expansions, caveats, skill instructions)
        # carry isMeta=true. They are not real prompts — treating them as turn starts
        # creates phantom turns and prematurely flushes the real one.
        if row.get("isMeta"):
            # Skill invocations link their injected instructions to the originating
            # tool_use via sourceToolUseID; keep the text so emit can optionally
            # attach it to that tool span.
            src = row.get("sourceToolUseID")
            if src:
                txt = extract_text(get_content_from_row(row))
                if txt:
                    injected_by_tool_id[str(src)] = txt
                    current_rows.append(row)
            continue

        role = get_user_or_assistant_role_from_row(row)

        # tool_result rows show up as role=user with content blocks of type tool_result
        if is_tool_result(row):
            current_rows.append(row)
            row_ts = row.get("timestamp")
            for tr in get_tool_result_blocks(get_content_from_row(row)):
                tid = tr.get("tool_use_id")
                if tid:
                    tool_results_by_id[str(tid)] = {"content": tr.get("content"), "timestamp": row_ts}
            continue

        task_tid = get_tool_use_id_from_task_notification(row)
        if task_tid:
            if current_turn_user_row is not None:
                existing_result = tool_results_by_id.get(task_tid)
                if isinstance(existing_result, dict):
                    existing_result["final_content"] = get_result_from_task_notification_row(row)
                    existing_result["final_timestamp"] = row.get("timestamp")
                else:
                    tool_results_by_id[task_tid] = {
                        "content": get_result_from_task_notification_row(row),
                        "timestamp": row.get("timestamp"),
                    }
                current_rows.append(row)
            else:
                current_turn_user_row = row
                current_rows = [row]
            continue

        if role == "user":
            # new user message -> finalize previous turn
            flush_turn()

            # start a new turn
            current_turn_user_row = row
            assistant_message_ids = []
            assistant_rows_by_message_id = {}
            tool_results_by_id = {}
            tool_use_timestamps_by_id = {}
            injected_by_tool_id = {}
            current_rows = [row]
            continue

        if role == "assistant":
            if current_turn_user_row is None:
                # ignore assistant rows until we see a user message
                continue

            message_id = get_message_id(row) or f"noid:{len(assistant_message_ids)}"
            if message_id not in assistant_rows_by_message_id:
                assistant_message_ids.append(message_id)
                assistant_rows_by_message_id[message_id] = []
            assistant_rows_by_message_id[message_id].append(row)
            for tool_use_block in get_tool_use_blocks(get_content_from_row(row)):
                tool_use_id = tool_use_block.get("id")
                if tool_use_id:
                    tool_use_timestamps_by_id.setdefault(str(tool_use_id), row.get("timestamp"))
            current_rows.append(row)
            continue

        # ignore unknown rows

    # flush last
    flush_turn()
    return turns

# ----------------- Langfuse emit -----------------
def _to_ns(ts: Optional[datetime]) -> Optional[int]:
    """Convert a datetime to OTel-style nanoseconds since epoch."""
    if ts is None:
        return None
    return int(ts.timestamp() * 1_000_000_000)


def _start_backdated(langfuse: Langfuse, *, name: str, as_type: str,
                     start_time: Optional[datetime],
                     parent_otel_span: Any = None,
                     **obs_kwargs: Any) -> Any:
    """Create a Langfuse observation with an explicit OTel start_time.

    Bypasses langfuse.start_observation() (which has no start_time kwarg in
    SDK 4.x) by talking to the underlying OTel tracer directly and then
    wrapping the resulting span with the Langfuse observation type.

    Depends on SDK 4.x internals: langfuse._otel_tracer and
    langfuse._create_observation_from_otel_span. If a future SDK version
    renames or removes these, raise a clear error instead of letting an
    AttributeError get swallowed by the broad emit_turn handler.
    """
    if not hasattr(langfuse, "_otel_tracer") or not hasattr(langfuse, "_create_observation_from_otel_span"):
        try:
            sdk_version = getattr(__import__("langfuse"), "__version__", "unknown")
        except Exception:
            sdk_version = "unknown"
        raise RuntimeError(
            f"Langfuse SDK {sdk_version} is missing _otel_tracer or "
            f"_create_observation_from_otel_span. This hook targets SDK 4.x; "
            f"pin with `pip install \"langfuse>=4.0,<5\"` or update the hook script."
        )
    start_ns = _to_ns(start_time)
    if parent_otel_span is not None:
        with otel_trace_api.use_span(parent_otel_span, end_on_exit=False):
            otel_span = langfuse._otel_tracer.start_span(name=name, start_time=start_ns)
    else:
        otel_span = langfuse._otel_tracer.start_span(name=name, start_time=start_ns)
    return langfuse._create_observation_from_otel_span(
        otel_span=otel_span,
        as_type=as_type,
        **obs_kwargs,
    )


def collect_skill_tags(turn: Turn) -> List[str]:
    """Return 'skill:<name>' tags for every Skill tool invocation in the turn."""
    names: List[str] = []
    for am in turn.assistant_msgs:
        for tu in get_tool_use_blocks(get_content_from_row(am)):
            if tu.get("name") != "Skill":
                continue
            tu_input = tu.get("input")
            skill = tu_input.get("skill") if isinstance(tu_input, dict) else None
            if isinstance(skill, str) and skill and f"skill:{skill}" not in names:
                names.append(f"skill:{skill}")
    return names


def short_session_label(session_id: str, max_len: int = 12) -> str:
    """Return a compact session label for trace names."""
    sid = session_id.strip()
    if not sid:
        return "unknown"
    parts = sid.split("-")
    if len(parts) == 5 and len(parts[0]) == 8:
        return parts[0]
    return sid if len(sid) <= max_len else sid[:max_len].rstrip("-")


def trace_display_name(session_id: str, turn_num: int) -> str:
    return f"Claude Code - Turn {turn_num} ({short_session_label(session_id)})"


def _get_latest_timestamp(*timestamps: Optional[datetime]) -> Optional[datetime]:
    present_timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    return max(present_timestamps) if present_timestamps else None


def emit_observations(langfuse: Langfuse, parent_otel_span: Any, turn: Turn,
                      start_ts: Optional[datetime],
                      generation_prefix: str = "Claude Generation",
                      subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None) -> Optional[datetime]:
    """Emit a turn's generations and tool observations under an existing span."""
    user_text, _ = truncate_text(extract_text(get_content_from_row(turn.user_msg)))
    prev_ts = start_ts
    prev_tool_results: List[Dict[str, Any]] = []
    pending_async_tool_results: List[Dict[str, Any]] = []
    pending_subagents: List[Dict[str, Any]] = []
    latest_end = start_ts

    for idx, am in enumerate(turn.assistant_msgs):
        am_ts = parse_timestamp(am)
        am_text_raw = extract_text(get_content_from_row(am))
        am_text, am_text_meta = truncate_text(am_text_raw)
        model = get_model(am)
        tool_uses = get_tool_use_blocks(get_content_from_row(am))
        ready_subagents: List[Dict[str, Any]] = []
        if idx > 0 and pending_subagents:
            still_pending_subagents: List[Dict[str, Any]] = []
            for pending_subagent in pending_subagents:
                ready_ts = pending_subagent.get("ready_timestamp")
                if isinstance(ready_ts, datetime) and (am_ts is None or ready_ts <= am_ts):
                    ready_subagents.append(pending_subagent)
                else:
                    still_pending_subagents.append(pending_subagent)
            pending_subagents = still_pending_subagents
            for ready_subagent in ready_subagents:
                subagent_end_ts = emit_subagent_observations(
                    langfuse,
                    parent_otel_span,
                    ready_subagent["subagent"],
                    ready_subagent.get("start_timestamp"),
                )
                latest_end = _get_latest_timestamp(latest_end, subagent_end_ts)

        ready_async_tool_results: List[Dict[str, Any]] = []
        if idx > 0 and pending_async_tool_results:
            still_pending: List[Dict[str, Any]] = []
            for async_result in pending_async_tool_results:
                async_ts = async_result.get("timestamp")
                if isinstance(async_ts, datetime) and (am_ts is None or async_ts <= am_ts):
                    ready_async_tool_results.append(async_result)
                else:
                    still_pending.append(async_result)
            pending_async_tool_results = still_pending
            if ready_async_tool_results:
                ready_ts = _get_latest_timestamp(*[
                    result.get("timestamp") for result in ready_async_tool_results
                    if isinstance(result.get("timestamp"), datetime)
                ])
                prev_ts = _get_latest_timestamp(prev_ts, ready_ts)

        if idx == 0:
            gen_input: Any = {"role": "user", "content": user_text}
        elif prev_tool_results:
            gen_input = {"role": "tool", "tool_results": prev_tool_results}
        elif ready_async_tool_results:
            gen_input = {
                "role": "tool",
                "tool_results": [result["tool_result"] for result in ready_async_tool_results],
            }
        else:
            gen_input = None

        gen_tool_calls = []
        for tu in tool_uses:
            gen_tool_calls.append({
                "id": tu.get("id"),
                "name": tu.get("name"),
            })

        gen_output: Dict[str, Any] = {"role": "assistant"}
        if am_text:
            gen_output["content"] = am_text
        if gen_tool_calls:
            gen_output["tool_calls"] = gen_tool_calls

        gen_kwargs: Dict[str, Any] = dict(
            model=model,
            input=gen_input,
            output=gen_output,
            metadata={
                "assistant_index": idx,
                "assistant_text": am_text_meta,
                "tool_count": len(tool_uses),
            },
        )
        usage_details = get_usage_details_from_row(am)
        if usage_details is not None:
            gen_kwargs["usage_details"] = usage_details

        gen_span = _start_backdated(
            langfuse,
            name=f"{generation_prefix} {idx + 1}",
            as_type="generation",
            start_time=prev_ts or am_ts,
            parent_otel_span=parent_otel_span,
            **gen_kwargs,
        )

        batch_result_ts: List[datetime] = []
        batch_tool_results: List[Dict[str, Any]] = []
        for tu in tool_uses:
            tid = str(tu.get("id") or "")
            tname = tu.get("name") or "unknown"
            tinput_raw = tu.get("input") if isinstance(tu.get("input"), (dict, list, str, int, float, bool)) else {}
            if isinstance(tinput_raw, str):
                tinput, tinput_meta = truncate_text(tinput_raw)
            else:
                tinput, tinput_meta = tinput_raw, None

            tr_entry = turn.tool_results_by_id.get(tid) if tid else None
            if tr_entry:
                out_raw = tr_entry.get("content")
                out_str = out_raw if isinstance(out_raw, str) else json.dumps(out_raw, ensure_ascii=False)
                out_trunc, out_meta = truncate_text(out_str)
                tr_ts = parse_timestamp(tr_entry.get("timestamp"))
                final_out_raw = tr_entry.get("final_content")
                if final_out_raw is not None:
                    final_out_str = final_out_raw if isinstance(final_out_raw, str) else json.dumps(final_out_raw, ensure_ascii=False)
                    final_out_trunc, final_out_meta = truncate_text(final_out_str)
                    final_tr_ts = parse_timestamp(tr_entry.get("final_timestamp"))
                else:
                    final_out_trunc, final_out_meta, final_tr_ts = None, None, None
            else:
                out_trunc, out_meta, tr_ts = None, None, None
                final_out_trunc, final_out_meta, final_tr_ts = None, None, None

            tool_output_trunc = out_trunc
            tool_output_meta = out_meta
            tool_output: Any = tool_output_trunc
            if CAPTURE_SKILL_CONTENT:
                injected = turn.injected_by_tool_id.get(tid) if tid else None
                if injected:
                    injected_trunc, _ = truncate_text(injected)
                    tool_output = {"result": tool_output_trunc, "injected_instructions": injected_trunc}

            sub = subagent_transcripts_by_tool_use_id.get(tid) if subagent_transcripts_by_tool_use_id and tid else None
            tool_meta: Dict[str, Any] = {
                "tool_name": tname,
                "tool_id": tid,
                "input_meta": tinput_meta,
                "output_meta": tool_output_meta,
            }
            if sub:
                tool_meta.update({
                    "subagent_type": sub.get("agent_type"),
                    "subagent_description": sub.get("description"),
                    "subagent_transcript_path": str(sub.get("path")),
                })

            tool_use_ts = parse_timestamp(turn.tool_use_timestamps_by_id.get(tid)) or am_ts
            tool_span = _start_backdated(
                langfuse,
                name=f"Tool: {tname}",
                as_type="tool",
                start_time=tool_use_ts,
                parent_otel_span=parent_otel_span,
                input=tinput,
                metadata=tool_meta,
            )
            tool_span.update(output=tool_output)

            subagent_end_ts = None
            if sub:
                if final_tr_ts is not None:
                    pending_subagents.append({
                        "subagent": sub,
                        "start_timestamp": tool_use_ts,
                        "ready_timestamp": final_tr_ts,
                    })
                else:
                    subagent_end_ts = emit_subagent_observations(langfuse, parent_otel_span, sub, tool_use_ts)

            tool_end_ts = _get_latest_timestamp(tr_ts, tool_use_ts)
            handoff_ts = tr_ts or final_tr_ts or subagent_end_ts or am_ts
            if handoff_ts is not None:
                batch_result_ts.append(handoff_ts)
            tool_span.end(end_time=_to_ns(tool_end_ts))
            latest_end = _get_latest_timestamp(latest_end, tool_end_ts, subagent_end_ts)

            batch_tool_results.append({
                "tool_use_id": tid,
                "tool_name": tname,
                "output": out_trunc,
            })
            if final_tr_ts is not None and final_out_trunc is not None:
                pending_async_tool_results.append({
                    "timestamp": final_tr_ts,
                    "tool_result": {
                        "tool_use_id": tid,
                        "tool_name": tname,
                        "output": final_out_trunc,
                    },
                })

        gen_end_ts = max(batch_result_ts) if batch_result_ts else am_ts
        gen_span.end(end_time=_to_ns(gen_end_ts or am_ts or prev_ts))
        latest_end = _get_latest_timestamp(latest_end, gen_end_ts)

        prev_tool_results = batch_tool_results
        if batch_result_ts:
            prev_ts = max(batch_result_ts)
        elif am_ts is not None:
            prev_ts = am_ts

    for pending_subagent in pending_subagents:
        subagent_end_ts = emit_subagent_observations(
            langfuse,
            parent_otel_span,
            pending_subagent["subagent"],
            pending_subagent.get("start_timestamp"),
        )
        latest_end = _get_latest_timestamp(latest_end, subagent_end_ts)

    return latest_end


def emit_subagent_observations(langfuse: Langfuse, parent_otel_span: Any,
                               subagent: Dict[str, Any],
                               start_ts: Optional[datetime]) -> Optional[datetime]:
    path = subagent.get("path")
    if not isinstance(path, Path):
        return start_ts
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception as e:
        info(f"subagent transcript read failed ({path}): {type(e).__name__}: {e}")
        return start_ts

    turns = build_turns(rows)
    if not turns:
        return start_ts

    first_turn = turns[0]
    subagent_start_ts = parse_timestamp(first_turn.user_msg) or start_ts
    subagent_input_text, subagent_input_meta = truncate_text(extract_text(get_content_from_row(first_turn.user_msg)))

    last_turn = turns[-1]
    last_assistant = last_turn.assistant_msgs[-1]
    subagent_output_text, _ = truncate_text(extract_text(get_content_from_row(last_assistant)))

    description = subagent.get("description")
    subagent_name = f"Subagent: {description}" if isinstance(description, str) and description else "Subagent"
    subagent_span = _start_backdated(
        langfuse,
        name=subagent_name,
        as_type="span",
        start_time=subagent_start_ts,
        parent_otel_span=parent_otel_span,
        input={"role": "user", "content": subagent_input_text},
        metadata={
            "agent_type": subagent.get("agent_type"),
            "description": description,
            "transcript_path": str(path),
            "user_text": subagent_input_meta,
        },
    )

    latest_end = subagent_start_ts
    prev_start = subagent_start_ts
    for turn in turns:
        latest = emit_observations(
            langfuse,
            subagent_span._otel_span,
            turn,
            prev_start,
            generation_prefix="Subagent Generation",
            subagent_transcripts_by_tool_use_id=None,
        )
        latest_end = _get_latest_timestamp(latest_end, latest)
        if latest is not None:
            prev_start = latest

    subagent_span.update(output={"role": "assistant", "content": subagent_output_text})
    subagent_span.end(end_time=_to_ns(_get_latest_timestamp(latest_end, subagent_start_ts)))

    return latest_end


def emit_turn(langfuse: Langfuse, session_id: str, turn_num: int, turn: Turn, transcript_path: Path,
              user_id: Optional[str] = None,
              subagent_transcripts_by_tool_use_id: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
    user_text_raw = extract_text(get_content_from_row(turn.user_msg))
    user_text, user_text_meta = truncate_text(user_text_raw)

    last_assistant = turn.assistant_msgs[-1]
    final_assistant_text, _ = truncate_text(extract_text(get_content_from_row(last_assistant)))

    user_ts = parse_timestamp(turn.user_msg)
    last_assistant_ts = parse_timestamp(last_assistant)
    # Pick a turn end_time: latest among final assistant message or any tool result
    candidate_end_ts = [t for t in [last_assistant_ts] if t is not None]
    for tr in turn.tool_results_by_id.values():
        t = parse_timestamp(tr)
        if t is not None:
            candidate_end_ts.append(t)
    turn_end_ts = max(candidate_end_ts) if candidate_end_ts else None

    trace_metadata: Dict[str, Any] = {
        "source": "claude-code",
        "session_id": session_id,
        "turn_number": turn_num,
        "transcript_path": str(transcript_path),
        "user_text": user_text_meta,
        "assistant_message_count": len(turn.assistant_msgs),
    }
    # Transcript rows carry the project dir and git branch — surface them so
    # traces from different projects/worktrees are distinguishable in Langfuse.
    for src_key, dst_key in (("cwd", "cwd"), ("gitBranch", "git_branch")):
        v = turn.user_msg.get(src_key)
        if isinstance(v, str) and v:
            trace_metadata[dst_key] = v

    tags = ["claude-code"]
    if SKILL_TAGS:
        tags += collect_skill_tags(turn)

    trace_name = trace_display_name(session_id, turn_num)
    root_observation_name = f"Turn {turn_num}"

    with propagate_attributes(
        session_id=session_id,
        user_id=user_id,
        trace_name=trace_name,
        tags=tags,
    ):
        trace_span = _start_backdated(
            langfuse,
            name=root_observation_name,
            as_type="span",
            start_time=user_ts,
            input={"role": "user", "content": user_text},
            metadata=trace_metadata,
        )
        obs_end_ts = emit_observations(
            langfuse,
            trace_span._otel_span,
            turn,
            user_ts,
            subagent_transcripts_by_tool_use_id=subagent_transcripts_by_tool_use_id,
        )
        trace_span.update(output={"role": "assistant", "content": final_assistant_text})
        trace_span.end(end_time=_to_ns(_get_latest_timestamp(turn_end_ts, last_assistant_ts, obs_end_ts, user_ts)))

# ----------------- Main -----------------
def main() -> int:
    start = time.time()
    debug("Hook started")

    config = get_langfuse_config()
    if config is None:
        return 0

    langfuse = create_langfuse_client(config)
    if langfuse is None:
        return 0

    payload = read_hook_payload()
    session_id, transcript_path = extract_session_id_and_transcript_path(payload)

    if not session_id or not transcript_path:
        # No structured payload; fail open (do not guess)
        debug("Missing session_id or transcript_path from hook payload; exiting.")
        return 0

    if not transcript_path.exists():
        debug(f"Transcript path does not exist: {transcript_path}")
        return 0

    try:
        with FileLock(LOCK_FILE):
            state = load_state()
            key = state_key(session_id, str(transcript_path))
            ss = load_session_state(state, key)

            msgs, ss = read_new_jsonl(transcript_path, ss)
            if not msgs:
                write_session_state(state, key, ss)
                save_state(state)
                return 0

            msgs = prepend_deferred_agent_turn_rows(msgs, ss)
            turns = build_turns(msgs)
            if not turns:
                write_session_state(state, key, ss)
                save_state(state)
                return 0

            subagent_transcripts_by_tool_use_id = get_subagent_transcripts_by_tool_use_id(transcript_path)
            if subagent_transcripts_by_tool_use_id:
                debug(f"Discovered {len(subagent_transcripts_by_tool_use_id)} subagent transcript(s)")

            turns_to_emit: List[Turn] = []
            for t in turns:
                pending_agent_tool_use_ids = get_pending_agent_tool_use_ids(t)
                if pending_agent_tool_use_ids:
                    for tool_use_id in pending_agent_tool_use_ids:
                        ss.pending_agent_turns[tool_use_id] = t.rows
                    debug(f"Deferred agent turn until task notification: {pending_agent_tool_use_ids}")
                    continue
                turns_to_emit.append(t)

            # emit turns
            emitted = 0
            for t in turns_to_emit:
                emitted += 1
                turn_num = ss.turn_count + emitted
                try:
                    emit_turn(langfuse, session_id, turn_num, t, transcript_path,
                              user_id=config.user_id, subagent_transcripts_by_tool_use_id=subagent_transcripts_by_tool_use_id)
                except Exception as e:
                    # Log at INFO so SDK incompatibilities (and other emit failures)
                    # are visible without needing CC_LANGFUSE_DEBUG=true.
                    info(f"emit_turn failed: {type(e).__name__}: {e}")
                    # continue emitting other turns

            ss.turn_count += emitted
            write_session_state(state, key, ss)
            save_state(state)

        dur = time.time() - start
        info(f"Processed {emitted} turns in {dur:.2f}s (session={session_id})")
        return 0

    except TimeoutError as e:
        debug(f"lock timeout, skipping: {e}")
        return 0

    except Exception as e:
        debug(f"Unexpected failure: {e}")
        return 0

    finally:
        # Cap flush+shutdown at 5s so a slow/unreachable Langfuse can't stall Claude Code.
        if langfuse is not None:
            try:
                def _flush_and_shutdown():
                    try:
                        langfuse.flush()
                    except Exception:
                        pass
                    langfuse.shutdown()
                t = threading.Thread(target=_flush_and_shutdown, daemon=True)
                t.start()
                t.join(5.0)
            except Exception:
                pass

if __name__ == "__main__":
    sys.exit(main())
