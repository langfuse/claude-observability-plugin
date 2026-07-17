# Langfuse Observability Plugin for Claude Code

Trace hook-backed Claude Code sessions to [Langfuse](https://langfuse.com) — turns, generations, tool calls, and token usage — with zero code changes.

## Install

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

The marketplace command registers the plugin marketplace and refreshes its local cache. The install command enables the plugin for your Claude Code user scope.

Restart Claude Code after install so the hook configuration is loaded.

Then configure the plugin from within a Claude Code session. This is a Claude Code slash command, not a shell command:

```text
/plugin configure langfuse-observability@langfuse-observability
```

Alternatively, pass configuration values during install:

```bash
claude plugin install langfuse-observability@langfuse-observability \
  --config LANGFUSE_PUBLIC_KEY=pk-lf-... \
  --config LANGFUSE_SECRET_KEY=sk-lf-... \
  --config LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

The plugin requires or accepts:

| Field                 | Description                                                                                          |
| --------------------- | ---------------------------------------------------------------------------------------------------- |
| `LANGFUSE_SECRET_KEY` | Your Langfuse secret key (`sk-lf-...`). Stored in your OS keychain.                                  |
| `LANGFUSE_PUBLIC_KEY` | Your Langfuse public key (`pk-lf-...`).                                                              |
| `LANGFUSE_BASE_URL`   | https://us.cloud.langfuse.com (default), https://cloud.langfuse.com for EU, or your self-hosted URL. |
| `LANGFUSE_USER_ID`    | Optional. User identifier attached to every trace (shown as the user in Langfuse).                   |
| `CC_LANGFUSE_DEBUG`   | Verbose logging to `~/.claude/state/langfuse_hook.log`.                                              |
| `CC_LANGFUSE_MAX_CHARS` | Truncate captured inputs/outputs to this many characters (default 20000).                          |
| `CC_LANGFUSE_SKILL_TAGS` | Tag traces with `skill:<name>` for every skill invoked in the turn (default true).                 |
| `CC_LANGFUSE_CAPTURE_SKILL_CONTENT` | Include injected skill instruction text in the Skill tool span output (default false).  |
| `CC_LANGFUSE_TRACE_SEED` | Optional. Opt-in seed for deterministic trace IDs — see [Deterministic trace IDs](#deterministic-trace-ids). |

Get keys from your Langfuse project settings → API Keys.

## Requirements

One of:

- [uv](https://docs.astral.sh/uv/) (recommended) on `PATH`. The hook uses `uv run --script` and installs the Langfuse SDK from the script metadata automatically.
- Python 3.10+ as `python3` with `langfuse>=4.0,<5` installed in that Python environment. This is only used as a fallback when `uv` is not on `PATH`.

If neither is set up, the hook exits silently — no impact on Claude Code.

## How it works

A hook reads the session transcript incrementally on every turn (Stop) and at session end (SessionEnd), and emits a Langfuse trace with one span per turn, nested generations per assistant message, and child tool spans for every tool call. Token usage is captured when present.

The plugin observes only data that Claude Code exposes through hooks and transcript files.

Captured:

- Claude Code CLI sessions
- Claude Code GUI Code mode sessions

Not captured:

- Regular Claude Desktop Chat mode conversations

If you use the desktop app, switch to Code mode for hook-backed tracing. Regular Chat mode does not invoke Claude Code Stop or SessionEnd hooks and does not write the transcript file this plugin reads.

State is kept in `~/.claude/state/langfuse_state.json` so re-runs only emit new turns.

## Deterministic trace IDs

By default, trace IDs are auto-generated, so external systems (CI harnesses, benchmark
runners, dataset-experiment services) that run Claude Code headlessly have to poll the
Langfuse API to discover the trace ID of a run. Setting `CC_LANGFUSE_TRACE_SEED` makes
trace IDs predictable: any system that knows the seed can compute the trace ID of each
turn **before** the trace exists.

With the seed set, the trace ID for turn `N` (1-based, counted per session across hook
runs) is derived as:

```python
from langfuse import Langfuse

trace_id = Langfuse.create_trace_id(seed=f"{CC_LANGFUSE_TRACE_SEED}:{turn_number}")
# equivalent to: hashlib.sha256(f"{seed}:{turn_number}".encode()).hexdigest()[:32]
```

Use a **unique seed per session** — otherwise two sessions collide on the same trace
IDs. The recommended seed is the Claude session UUID you already pass via
`claude -p --session-id`.

Example: a harness runs a single-turn headless prompt and links the resulting trace to
a Langfuse dataset run item without ever fetching the trace:

```python
import subprocess, uuid, os
from langfuse import Langfuse

langfuse = Langfuse()
session_id = str(uuid.uuid4())

# Precompute the trace ID for turn 1 — no polling needed.
trace_id = Langfuse.create_trace_id(seed=f"{session_id}:1")

# Link it to a dataset run item BEFORE the run.
langfuse.api.dataset_run_items.create(
    dataset_item_id=item.id,
    run_name="claude-code-benchmark-v1",
    trace_id=trace_id,
)

subprocess.run(
    ["claude", "-p", "Refactor utils.py", "--session-id", session_id],
    env={**os.environ, "CC_LANGFUSE_TRACE_SEED": session_id},
)
# When the hook uploads the turn, its trace appears in Langfuse under trace_id.
```

If derivation or wiring fails for any reason, the hook falls back to an auto-generated
trace ID (fail-open, like the rest of the hook). Unset or empty seed = unchanged behavior.

## Privacy

This plugin transmits your Claude Code session data — conversation turns, assistant
generations, tool calls, and token-usage statistics — to the Langfuse endpoint you
configure (`LANGFUSE_BASE_URL`, default `https://us.cloud.langfuse.com`; EU and
self-hosted endpoints are supported). Data is sent at the end of each turn (the
`Stop` hook) and at session end (`SessionEnd`) using the Langfuse API keys you
provide, which are stored in your OS keychain. No data is sent anywhere other than
the endpoint you configure.

For how Langfuse Cloud handles data it receives, see the Langfuse privacy policy:
https://langfuse.com/privacy . When using a self-hosted Langfuse instance, your data
stays within your own infrastructure.

## Reconfigure

In Claude Code, run:

```text
/plugin configure langfuse-observability@langfuse-observability
```

## Disable tracing

To stop tracing for new Claude Code CLI sessions and new Claude Code GUI Code mode sessions, disable the plugin:

```bash
claude plugin disable langfuse-observability@langfuse-observability --scope user
claude plugin list
```

`claude plugin list` should show the plugin as disabled. This keeps the plugin installed and does not delete your configuration.

To enable tracing again:

```bash
claude plugin enable langfuse-observability@langfuse-observability --scope user
```

If a Claude Code session is already running, restart it after disabling the plugin. Running sessions may have loaded their hooks at startup, so disabling the plugin is intended for new sessions.

## Uninstall

```bash
claude plugin uninstall langfuse-observability
```

## Troubleshooting

- Nothing in Langfuse: check `~/.claude/state/langfuse_hook.log` (enable `CC_LANGFUSE_DEBUG`).
- Desktop chat has no traces: regular Claude Desktop Chat mode is not hook-backed. Use the `claude` CLI or Claude Code GUI Code mode.
- Hook not firing: confirm with `claude plugin list` that langfuse-observability is enabled; restart Claude Code.
- langfuse import errors (no uv): install uv, or ensure the `python3` on your PATH is Python 3.10+ and has `langfuse>=4.0,<5` installed.

## License

MIT
