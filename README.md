# Langfuse Observability Plugin for Claude Code

Trace hook-backed Claude Code sessions to [Langfuse](https://langfuse.com) — turns, generations, tool calls, and token usage — with zero code changes.

## Install

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

The marketplace command registers the plugin marketplace and refreshes its local cache. The install command enables the plugin for your Claude Code user scope.

Restart Claude Code after install so the hook configuration is loaded.

Then configure the plugin from within a Claude Code session. This is a Claude Code slash command, not a shell command. Run `claude` first, then type it at the session prompt:

```text
$ claude
> /plugin configure langfuse-observability@langfuse-observability
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

- [uv](https://docs.astral.sh/uv/) (recommended) on `PATH`. The hook uses `uv run --script` and installs the Langfuse SDK from the script metadata automatically. In addition to your `PATH`, the hook also looks in the standard install locations `~/.local/bin`, `/opt/homebrew/bin`, and `/usr/local/bin`.
- Python 3.10+ as `python3` with `langfuse>=4.0,<5` installed in that Python environment. This is only used as a fallback when `uv` is not on `PATH`.

On the first hook run, uv downloads the Langfuse SDK from PyPI (declared in the script header) and caches it. Offline or proxied machines fail this download and retry on every turn until the cache is warm; you can pre-warm it from a networked terminal:

```bash
echo '{}' | uv run --quiet --script <plugin-root>/hooks/langfuse_hook.py
```

If no usable runtime is set up, the hook exits without tracing, and Claude Code is never blocked or slowed. Failure reasons are written to `~/.claude/state/langfuse_hook.log` (see Troubleshooting).

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

Nearly every failure explains itself in `~/.claude/state/langfuse_hook.log`. Send one message, then match the newest lines against this table:

| What the log shows | Meaning | What to do |
| --- | --- | --- |
| No new lines at all | The hook never launched. Either the plugin is disabled, no usable runtime was found (no `uv`, no suitable `python3`), or uv could not download the SDK (offline/proxy) | Run `claude plugin list` **from the directory you use in the app**, since enable/disable can be project-scoped. Install uv in a standard location. Pre-warm the SDK download (see Requirements) |
| `langfuse import failed (…) python=… PATH=…` | The Python that ran the hook cannot import the SDK; the line names the interpreter, version, and PATH | Install uv in a standard location, or make sure `python3` is 3.10+ with `langfuse>=4.0,<5` installed |
| `Langfuse config incomplete: missing …` | The named key(s) did not reach the hook. Either not configured yet, or configured but not delivered | Configure via `/plugin configure`. If the line also says `loaded under plugin identity '@inline'`, see the desktop section below |
| `Hook started` plus a skip reason (e.g. `Transcript path does not exist`) | The hook ran and skipped intentionally; these often come from background/utility sessions | Usually harmless. File an issue with the log line if actual turns are missing |
| `Processed N turns …` but nothing in Langfuse | Turns were handed to the SDK but delivery failed afterwards; export errors go to stderr, not this log | Check `LANGFUSE_BASE_URL` (EU `https://cloud.langfuse.com` vs US `https://us.cloud.langfuse.com`), key validity, and network/proxy reachability |

`Hook started` and other `[DEBUG]` lines require debug logging (`CC_LANGFUSE_DEBUG=true` via `/plugin configure`); the failure lines above are written at `[INFO]` and appear without it. Regular Claude Desktop **Chat** mode is not hook-backed. Tracing covers the `claude` CLI and desktop **Code** mode.

### Desktop app (GUI) sessions

GUI apps do not read your shell profile. `export`ed variables in `~/.zshrc` never reach GUI-spawned hooks, and the app resolves its `PATH` once at launch. After installing uv, fully quit and relaunch the desktop app. Keys must be configured via `/plugin configure` (or `--config` at install), not via shell exports.

### Plugin options not delivered in GUI sessions

Recent Claude Desktop builds load user-installed plugins under a different plugin identity (`langfuse-observability@inline`), so keys configured via `/plugin configure` are not delivered to the hook. Terminal sessions work, desktop sessions stay silent. When this happens, the log line contains `loaded under plugin identity '@inline'`.

As a **temporary workaround**, duplicate your full options under the inline identity in `~/.claude/settings.json`:

```json
"pluginConfigs": {
  "langfuse-observability@inline": {
    "options": {
      "LANGFUSE_PUBLIC_KEY": "pk-lf-...",
      "LANGFUSE_SECRET_KEY": "sk-lf-...",
      "LANGFUSE_BASE_URL": "https://cloud.langfuse.com"
    }
  }
}
```

The entry must include `LANGFUSE_SECRET_KEY` because the OS-keychain secret only applies to the installed identity. This stores the secret in plaintext, so consider a dedicated API key pair for the workaround period and remove the entry once it is no longer needed. No restart is needed; settings are picked up on the next message.

## License

MIT
