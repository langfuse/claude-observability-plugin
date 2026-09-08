# Langfuse Observability Plugin for Claude Code

This plugin sends [Claude Code](https://claude.com/claude-code) sessions to
[Langfuse](https://langfuse.com). It records the user prompts, the agent turns,
the model generations with their tokens and cost, and the tool calls, with no
change to your code.

Langfuse also documents this integration on the
[Claude Code integration page](https://langfuse.com/integrations/developer-tools/claude-code).

## What can this integration trace?

The plugin runs as a Claude Code hook and reads the session transcript on every
turn, so tracing needs no change to the way you work:

- **Agent turns**: one trace per user prompt, with all turns of a session grouped
  under one session ID.
- **Model generations**: every assistant message with inputs, outputs, cost and
  token usage, including cache reads and a cache write split by the lifetime it
  was written under, which each carry their own rate.
  token usage, including cache-read and reasoning splits.
- **Thinking**: assistant thinking blocks attach to the generation that produced
  them and render as thinking blocks in the trace. Claude Code writes the
  thinking text only when `showThinkingSummaries` is `true` in its settings.
- **Tool calls**: each tool Claude Code invokes, with input and output.
- **Subagents**: subagent and Workflow-spawned agent transcripts nest under the
  turn that started them.
- **Skills**: traces carry a `skill:<name>` tag for every skill a turn invokes.
- **Images**: pasted images and screenshots from tool results upload as Langfuse
  media and render inside the trace.

Tracing covers the `claude` CLI and desktop **Code** mode; regular Claude Desktop
**Chat** mode runs no Claude Code hooks and is not traced. 

## Prerequisites

One of:

- [uv](https://docs.astral.sh/uv/) (recommended) on `PATH`. The hook uses
  `uv run --script` and installs the Langfuse SDK from the script metadata.
- Python 3.10+ as `python3` with `langfuse>=4.7,<5` installed. This is only a
  fallback for when `uv` is not on `PATH`.

On the first hook run, uv downloads the SDK from PyPI and caches it. An offline
or proxied machine retries that download every turn until the cache is warm, so
pre-warm it from a networked terminal:

```bash
echo '{}' | uv run --quiet --script <plugin-root>/hooks/langfuse_hook.py
```

Without a usable runtime the hook exits without tracing, never blocks or slows
Claude Code, and logs the reason, see [Troubleshooting](#troubleshooting).

## Install

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

The marketplace command registers the plugin marketplace and refreshes its local
cache. The install command enables the plugin for your Claude Code user scope.
Restart Claude Code afterwards so the hook configuration is loaded.

## Add your Langfuse credentials

Configure the plugin from inside a Claude Code session. This is a Claude Code
slash command, not a shell command:

```text
$ claude
> /plugin configure langfuse-observability@langfuse-observability
```

Alternatively, pass the values during install:

```bash
claude plugin install langfuse-observability@langfuse-observability \
  --config LANGFUSE_PUBLIC_KEY=pk-lf-... \
  --config LANGFUSE_SECRET_KEY=sk-lf-... \
  --config LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

Only `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are required; without
`LANGFUSE_BASE_URL` the plugin uses `https://cloud.langfuse.com` (EU region).
Get keys from your Langfuse project settings → API Keys. The secret key is held
in your OS keychain, not in a file.

## Configuration options

Set these through `/plugin configure` or `--config`, unless the table says the
value is a per-run environment variable.

| Option | Description | Required |
| ------ | ----------- | -------- |
| `LANGFUSE_SECRET_KEY` | Your Langfuse secret key (`sk-lf-...`). Held in your OS keychain. | Yes |
| `LANGFUSE_PUBLIC_KEY` | Your Langfuse public key (`pk-lf-...`). | Yes |
| `LANGFUSE_BASE_URL` | Langfuse host. EU: `https://cloud.langfuse.com`, US: `https://us.cloud.langfuse.com`, or your self-hosted URL. | No (defaults to EU) |
| `LANGFUSE_USER_ID` | User identifier attached to every trace, shown as the user in Langfuse. | No |
| `CC_LANGFUSE_DEBUG` | Verbose logging to the hook log (default `false`). | No |
| `CC_LANGFUSE_MAX_CHARS` | Truncate captured inputs and outputs to this many characters (default `20000`). | No |
| `CC_LANGFUSE_SKILL_TAGS` | Tag traces with `skill:<name>` for every skill invoked in the turn (default `true`). | No |
| `CC_LANGFUSE_CAPTURE_SKILL_CONTENT` | Include injected skill instruction text in the Skill tool span output (default `false`). | No |
| `CC_LANGFUSE_CAPTURE_IMAGES` | Upload images to Langfuse and show them in the trace (default `true`). Needs media upload on your deployment (self-hosted: `LANGFUSE_S3_MEDIA_UPLOAD_*`). Set it to `false` if media upload is unavailable: the trace then shows a marker per image, such as `[image image/png ~200KB]`. | No |
| `CC_LANGFUSE_FLUSH_TIMEOUT` | Seconds the hook waits for the SDK to upload the turn's events before giving up (default `120`). A long session produces a payload that takes far longer than a few seconds to upload, and giving up early drops the whole session silently — the hook now logs when it gives up. Lower it if you would rather an unreachable Langfuse never delay the end of a turn. An unusable value falls back to the default and logs. At `SessionEnd` Claude Code caps the wait at 60s regardless of this value (see below). | No |
| `CC_LANGFUSE_STATE_DIR` | Absolute directory (`~` is expanded) for the hook's state, lock and log files (default `~/.claude/state`). Set one per `CLAUDE_CONFIG_DIR` installation to keep them apart. An unusable value falls back to the default and logs a warning. | No |
| `CC_LANGFUSE_TRACE_SEED` | Seed that makes trace IDs predictable, so a headless caller can derive a run's trace ID before the trace exists. Use a unique seed per session, otherwise sessions collide on the same trace IDs. | No |
| `CC_LANGFUSE_TRACEPARENT` | Per-run environment variable. W3C traceparent of an existing trace to attach to — see [Attach runs to an existing trace](#attach-runs-to-an-existing-trace). | No |
| `CC_LANGFUSE_PARENT_TRACE_ID` / `CC_LANGFUSE_PARENT_SPAN_ID` | Per-run environment variables. Explicit alternative to `CC_LANGFUSE_TRACEPARENT` (32-hex trace id plus 16-hex span id). | No |

## Update

Updates are manual since auto-update is off by default for marketplaces outside Anthropic's.

```bash
claude plugin marketplace update langfuse-observability
claude plugin update langfuse-observability@langfuse-observability
```

The marketplace command pulls the current code from GitHub into the local
marketplace clone. Restart Claude Code
afterwards so the new hook is loaded.

To see which version is installed:

```bash
claude plugin list
```

## Enable and disable tracing

| Scope | How |
| ----- | --- |
| Change the settings | `/plugin configure langfuse-observability@langfuse-observability` in a session |
| Stop tracing new sessions | `claude plugin disable langfuse-observability@langfuse-observability --scope user` |
| Start tracing again | `claude plugin enable langfuse-observability@langfuse-observability --scope user` |
| Remove the plugin | `claude plugin uninstall langfuse-observability` |

Disabling keeps the plugin installed and keeps your configuration. Check the
result with `claude plugin list`.

A session that already runs loads its hooks at startup, so restart it after you
disable the plugin. Enable and disable can be project-scoped, so run
`claude plugin list` from the directory you work in.

## Attach runs to an existing trace

When your application launches Claude Code headlessly (`claude -p`) as one step
of an already-instrumented workflow, the run can join your existing Langfuse
trace instead of creating its own root trace. Create a span for the agent run,
then pass its trace context when you launch Claude Code:

```python
with langfuse.start_as_current_span(name="Claude Code run") as run_span:
    traceparent = f"00-{run_span.trace_id}-{run_span.id}-01"
    subprocess.run(
        ["claude", "-p", "Refactor utils.py"],
        env={**os.environ, "CC_LANGFUSE_TRACEPARENT": traceparent},
    )
```

Every turn of the session, with its model calls, tool calls and subagents, then
appears under your `Claude Code run` span. `CC_LANGFUSE_PARENT_TRACE_ID` and
`CC_LANGFUSE_PARENT_SPAN_ID` are an explicit alternative to the traceparent.

## Troubleshooting

Nearly every failure explains itself in `~/.claude/state/langfuse_hook.log`, or
in `CC_LANGFUSE_STATE_DIR` if you set a usable one. Send one message, then match
the newest lines against this table:

| What the log shows | What to do |
| ------------------ | ---------- |
| No new lines at all | The hook never launched: the plugin is disabled, no usable runtime was found, or uv could not download the SDK. Run `claude plugin list` from the directory you use, and put uv on the `PATH` of the app that launches Claude Code. |
| `langfuse import failed (…) python=… PATH=…` | The Python that ran the hook cannot import the SDK. The line names the interpreter and PATH. Install uv on that PATH, or make `python3` a 3.10+ environment with `langfuse>=4.7,<5`. |
| `Langfuse config incomplete: missing …` | The named keys did not reach the hook. Configure them with `/plugin configure`. If the line also says `loaded under plugin identity '@inline'`, see below. |
| `Hook started` plus a skip reason | The hook ran and skipped on purpose, which is usual for background sessions. Report it with the log line if real turns are missing. |
| `Processed N turns …` but nothing in Langfuse | Delivery failed after the SDK took the turns. Check `LANGFUSE_BASE_URL` (EU against US), key validity, and proxy reachability. |
| `Langfuse flush did not finish within …s` | The upload was still running when the hook gave up, so some or all of that session's events never left the machine. Long sessions and slow links need longer: raise `CC_LANGFUSE_FLUSH_TIMEOUT`. |

`Hook started` and other `[DEBUG]` lines need `CC_LANGFUSE_DEBUG`. The failure
lines above are `[INFO]` and appear without it.

### Long sessions and the flush window

The hook hands the turn's events to the SDK and then waits for the upload,
capped by `CC_LANGFUSE_FLUSH_TIMEOUT` (default 120s) so an unreachable Langfuse
cannot stall Claude Code forever. A few hundred transcript rows produce an OTLP
payload that takes well over a few seconds to ship, so a short cap loses whole
sessions — silently before this was logged.

`SessionEnd` has a second, tighter budget that this plugin does not control:
Claude Code allows its `SessionEnd` hooks the longest `timeout` any of them
declares, floored at 1.5s and **capped at 60s**. `hooks/hooks.json` therefore
declares `"timeout": 60` on the `SessionEnd` entry to claim that whole ceiling;
without it the window is 1.5s and nearly nothing gets uploaded at session end.
`Stop` has no such ceiling, so it is where large sessions actually finish
flushing. (Verified against Claude Code 2.1.263.)

### Desktop app (GUI) sessions

A GUI app does not read your shell profile and resolves `PATH` once at launch, so
a variable you `export` in `~/.zshrc` never reaches a GUI-spawned hook. After you
install uv, fully quit and relaunch the app, and set keys through
`/plugin configure` rather than shell exports.

Recent Claude Desktop builds also load user-installed plugins under a second
plugin identity (`langfuse-observability@inline`), so keys from
`/plugin configure` never reach the hook: terminal sessions work while desktop
sessions stay silent. The log line then contains
`loaded under plugin identity '@inline'`. As a temporary workaround, repeat your
options under that identity in `~/.claude/settings.json`:

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

The entry needs `LANGFUSE_SECRET_KEY`, because the keychain secret applies only
to the installed identity. It is plain text, so use a dedicated key pair and
remove the entry once it is no longer needed. Settings are read on the next
message, with no restart.

## Development

```bash
uv run --group dev pytest
```

The hook is a single uv script with inline dependency metadata, and Claude Code
runs `hooks/langfuse_hook.py` directly, so there is no build step.

## License

[MIT](./LICENSE)
