#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "langfuse>=4.7,<5",
# ]
# ///
"""Re-send Claude Code turns that never reached Langfuse.

Hook versions before the commit-after-flush fix saved their progress before
the export finished, so turns from dropped exports were never retried
(langfuse/Claude-Observability-Plugin#103). This script finds local
transcripts, asks Langfuse which turn numbers it already has for each
session, and emits only the missing turns. Turns that are already present
are never re-sent (their span ids were random, so re-sending would
duplicate them).

Usage (dry run by default):
    LANGFUSE_SECRET_KEY=sk-lf-... uv run --script scripts/backfill_missing_turns.py [--days 30] [--apply]

Public key, base URL and user id are read from the environment or, if unset,
from the langfuse-observability plugin config in ~/.claude/settings.json.
The live hook's state file is never touched.
"""

from __future__ import annotations

import argparse
import ast
import base64
import importlib.util
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

ROOT_NAME = "Conversational Turn"
PLUGIN_KEY = "langfuse-observability@langfuse-observability"


def plugin_options() -> Dict[str, Any]:
    try:
        settings = json.loads((Path.home() / ".claude" / "settings.json").read_text())
        return settings.get("pluginConfigs", {}).get(PLUGIN_KEY, {}).get("options", {}) or {}
    except Exception:
        return {}


def resolve(name: str, options: Dict[str, Any]) -> str:
    return os.environ.get(name) or str(options.get(name) or "")


class LangfuseReader:
    def __init__(self, host: str, public_key: str, secret_key: str) -> None:
        self.base = host.rstrip("/") + "/api/public"
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}"}

    def _get(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(5):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError(f"Langfuse API kept failing for {path}")

    def existing_turn_numbers(self, session_id: str, since: datetime) -> Set[int]:
        found: Set[int] = set()
        cursor = None
        while True:
            params: Dict[str, Any] = {
                "fromStartTime": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "toStartTime": (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": 1000,
                "isRootObservation": "true",
                "fields": "core,metadata",
                "filter": json.dumps([
                    {"type": "string", "column": "sessionId", "operator": "=", "value": session_id},
                    {"type": "string", "column": "name", "operator": "=", "value": ROOT_NAME},
                ]),
            }
            if cursor:
                params["cursor"] = cursor
            data = self._get("/v2/observations", params)
            for row in data.get("data", []):
                metadata = row.get("metadata")
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except ValueError:
                        try:
                            metadata = ast.literal_eval(metadata)
                        except Exception:
                            metadata = {}
                try:
                    found.add(int((metadata or {}).get("turn_number")))
                except (TypeError, ValueError):
                    pass
            cursor = (data.get("meta") or {}).get("cursor")
            if not cursor:
                return found


def load_hook(state_dir: Path) -> Any:
    # The hook resolves its state dir at import time: isolate it first.
    os.environ["CC_LANGFUSE_STATE_DIR"] = str(state_dir)
    os.environ["CC_LANGFUSE_NO_DETACH"] = "true"
    hook_path = Path(__file__).resolve().parents[1] / "hooks" / "langfuse_hook.py"
    spec = importlib.util.spec_from_file_location("langfuse_hook_backfill", hook_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def first_timestamp(path: Path) -> Optional[datetime]:
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                ts = json.loads(line).get("timestamp")
            except ValueError:
                continue
            if isinstance(ts, str):
                try:
                    return datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30, help="only transcripts modified in the last N days")
    parser.add_argument("--session", help="only this session id")
    parser.add_argument("--apply", action="store_true", help="actually send (default: dry run)")
    parser.add_argument("--all-turns", action="store_true",
                        help="re-send every turn, not just missing ones. Only for sessions sent entirely "
                             "by this version (deterministic ids); older turns would be duplicated")
    parser.add_argument("--flush-timeout", type=float, default=1800.0,
                        help="seconds to wait for one session's export (long sessions are large)")
    parser.add_argument("--skip-active-minutes", type=int, default=60,
                        help="skip transcripts modified this recently (the live hook still owns them)")
    parser.add_argument("--projects-dir", default=str(Path.home() / ".claude" / "projects"))
    args = parser.parse_args()

    options = plugin_options()
    public_key = resolve("LANGFUSE_PUBLIC_KEY", options)
    secret_key = resolve("LANGFUSE_SECRET_KEY", options)
    host = resolve("LANGFUSE_BASE_URL", options) or "https://cloud.langfuse.com"
    user_id = resolve("LANGFUSE_USER_ID", options) or None
    if not public_key or not secret_key:
        print("Set LANGFUSE_SECRET_KEY (and LANGFUSE_PUBLIC_KEY if the plugin config lacks it).", file=sys.stderr)
        return 2
    os.environ.update({"LANGFUSE_PUBLIC_KEY": public_key, "LANGFUSE_SECRET_KEY": secret_key, "LANGFUSE_BASE_URL": host})
    if user_id:
        os.environ["LANGFUSE_USER_ID"] = user_id
    print(f"Langfuse {host} | user_id={user_id or '(none)'} | {'APPLY' if args.apply else 'dry run'}")

    reader = LangfuseReader(host, public_key, secret_key)
    now = time.time()
    cutoff = now - args.days * 86400
    active_cutoff = now - args.skip_active_minutes * 60
    transcripts = sorted(
        p for p in Path(args.projects_dir).glob("*/*.jsonl")
        if cutoff <= p.stat().st_mtime < active_cutoff and (not args.session or p.stem == args.session)
    )

    state_dir = Path(tempfile.mkdtemp(prefix="langfuse-backfill-"))
    hook = load_hook(state_dir)
    config = hook.get_langfuse_config()
    langfuse = hook.create_langfuse_client(config) if args.apply else None
    original_emit_turn = hook.emit_turn

    totals = {"sessions": 0, "missing": 0, "sent": 0, "failed_sessions": 0}
    for path in transcripts:
        session_id = path.stem
        started = first_timestamp(path)
        if started is None:
            continue
        try:
            present = reader.existing_turn_numbers(session_id, started - timedelta(days=1))
        except Exception as e:
            print(f"  {session_id}: could not query Langfuse ({e}); skipped")
            continue

        planned: list = []

        def selective_emit_turn(lf, sid, turn_num, turn, *a, **kw):
            if turn_num in present and not args.all_turns:
                return dict(kw.get("progress") or {})
            planned.append(turn_num)
            if not args.apply:
                return dict(kw.get("progress") or {})
            return original_emit_turn(lf, sid, turn_num, turn, *a, **kw)

        hook.emit_turn = selective_emit_turn
        ledger = hook.EXPORT_LEDGER
        with ledger.lock:
            ledger.exported = ledger.failed = ledger.oversized = ledger.ended = 0
        confirmed = {"ok": True}

        def gate() -> bool:
            confirmed["ok"] = hook.confirm_export(langfuse, timeout_s=args.flush_timeout) if args.apply else True
            return confirmed["ok"]

        try:
            hook.emit_new_turns_from_transcript(
                langfuse, config, session_id, path,
                flush_deferred_agent_turns=True, commit_gate=gate, lock_timeout_s=30.0,
            )
        except Exception as e:
            print(f"  {session_id}: emission failed ({type(e).__name__}: {e})")
            totals["failed_sessions"] += 1
            continue
        finally:
            hook.emit_turn = original_emit_turn

        if planned:
            totals["sessions"] += 1
            totals["missing"] += len(planned)
            status = "sent" if args.apply and confirmed["ok"] else ("FAILED" if args.apply else "would send")
            if args.apply and confirmed["ok"]:
                totals["sent"] += len(planned)
            if args.apply and not confirmed["ok"]:
                totals["failed_sessions"] += 1
            print(f"  {session_id}: {len(present)} present, {len(planned)} missing -> {status}")

    if langfuse is not None:
        hook.flush_and_shutdown_langfuse_client(langfuse)
    print(
        f"\n{totals['sessions']} session(s) with missing turns, {totals['missing']} missing turn(s)"
        + (f", {totals['sent']} sent, {totals['failed_sessions']} session(s) failed" if args.apply else "")
    )
    if not args.apply and totals["missing"]:
        print("Re-run with --apply to send them.")
    return 1 if totals["failed_sessions"] else 0


if __name__ == "__main__":
    sys.exit(main())
