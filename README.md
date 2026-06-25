# Langfuse Observability Plugin for Claude Code

A [Claude Code](https://docs.claude.com/en/docs/claude-code) plugin that traces every session — turns, generations, tool calls, and token usage — to [Langfuse](https://langfuse.com) with zero code changes.

Once enabled, each Claude Code turn shows up in Langfuse as a trace you can inspect, debug, evaluate, and monitor for cost — turning Claude Code from a black box into an observable agent.

## What gets traced

After each turn, a `Stop` hook reads the new part of the session transcript and uploads it to Langfuse as a [trace](https://langfuse.com/docs/observability/data-model). The structure mirrors how Claude Code actually works:

- **Turn** (`Claude Code Turn`, an agent observation) — one trace per turn, from your prompt to the final answer.
- **Generations** — one per assistant message within the turn, with the model name, assistant text, the tool calls it requested, and token usage (including cache reads/writes).
- **Tool calls** — `Bash`, `Read`, `Edit`, MCP tools, etc., each nested under the generation that issued it, with its input and output.
- **Subagents** — when a turn spawns a subagent (the `Agent`/`Task` tool), the subagent's own transcript is expanded inline and nested under the spawning tool call: its generations, token usage, and tool calls all show up as children.
- **Sessions** — all turns from one Claude Code session are grouped via the session id, so you can replay the whole session in Langfuse's [Sessions](https://langfuse.com/docs/observability/features/sessions) view.
- **Skills** — turns that invoke a skill are tagged `skill:<name>` (disable with `CC_LANGFUSE_SKILL_TAGS=false`), and the injected skill instructions can optionally be captured on the Skill tool span (`CC_LANGFUSE_CAPTURE_SKILL_CONTENT=true`).

Original timestamps are preserved on every span, so the Langfuse timeline reflects real wall-clock timing.

## Prerequisites

- [Bun](https://bun.sh) >= 1.2
- A [Langfuse Cloud](https://cloud.langfuse.com) account (or a [self-hosted](https://langfuse.com/self-hosting) instance) and API keys

No Python and no `pip install` — the hook ships as a single self-contained JavaScript bundle that Bun runs directly; no `node_modules`, no install step.

## Installation

### 1. Add the plugin marketplace

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
```

### 2. Install the plugin

```bash
claude plugin install langfuse-observability@langfuse-observability
```

Restart Claude Code after installing.

### 3. Configure your Langfuse credentials

On install you'll be prompted for:

| Field                 | Description                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| `LANGFUSE_SECRET_KEY` | Your Langfuse secret key (`sk-lf-...`). Stored in your OS keychain.                                      |
| `LANGFUSE_PUBLIC_KEY` | Your Langfuse public key (`pk-lf-...`).                                                                  |
| `LANGFUSE_BASE_URL`   | `https://us.cloud.langfuse.com` (default), `https://cloud.langfuse.com` for EU, or your self-hosted URL. |
| `CC_LANGFUSE_DEBUG`   | Verbose logging to stderr (off by default).                                                              |

Tracing is active as soon as the public and secret keys are set — there is no separate enable flag.

### 4. Get your Langfuse API keys

1. Go to [cloud.langfuse.com](https://cloud.langfuse.com) (or your self-hosted instance).
2. Create a project (or open an existing one).
3. Go to **Settings → API Keys → Create new API keys**.
4. Copy the **public** key (`pk-lf-...`) and **secret** key (`sk-lf-...`).

Run a Claude Code turn, then open your Langfuse project to see the trace.

## Configuration

Configuration is resolved as **defaults → `~/.claude/langfuse.json` → `<cwd>/.claude/langfuse.json` → environment variables** (environment wins). For each setting, the Claude Code plugin-config form (`CLAUDE_PLUGIN_OPTION_<NAME>`, set by the install prompt) takes precedence over the matching plain environment variable.

### Environment variables

| Variable                                                   | Required | Default                         | Description                                                |
| ---------------------------------------------------------- | -------- | ------------------------------- | ---------------------------------------------------------- |
| `LANGFUSE_PUBLIC_KEY` / `CC_LANGFUSE_PUBLIC_KEY`           | Yes      | —                               | Langfuse public key (`pk-lf-...`)                          |
| `LANGFUSE_SECRET_KEY` / `CC_LANGFUSE_SECRET_KEY`           | Yes      | —                               | Langfuse secret key (`sk-lf-...`)                          |
| `LANGFUSE_BASE_URL` / `CC_LANGFUSE_BASE_URL`               | No       | `https://us.cloud.langfuse.com` | Langfuse host / data region                                |
| `LANGFUSE_TRACING_ENVIRONMENT` / `CC_LANGFUSE_ENVIRONMENT` | No       | —                               | Environment label for the traces (e.g. `production`)       |
| `LANGFUSE_USER_ID` / `CC_LANGFUSE_USER_ID`                 | No       | Claude Code account email       | Attach a user id to all traces                             |
| `CC_LANGFUSE_TAGS`                                         | No       | —                               | Extra tags for all traces (JSON array or comma-separated)  |
| `CC_LANGFUSE_METADATA`                                     | No       | —                               | JSON object of metadata to attach to all traces            |
| `CC_LANGFUSE_MAX_CHARS`                                    | No       | `20000`                         | Truncate inputs/outputs longer than this many characters   |
| `CC_LANGFUSE_SKILL_TAGS`                                   | No       | `true`                          | Tag traces `skill:<name>` for every skill invoked          |
| `CC_LANGFUSE_CAPTURE_SKILL_CONTENT`                        | No       | `false`                         | Capture injected skill instructions on the Skill tool span |
| `CC_LANGFUSE_DEBUG`                                        | No       | `false`                         | Set to `"true"` for verbose logging to stderr              |
| `CC_LANGFUSE_FAIL_ON_ERROR`                                | No       | `false`                         | Set to `"true"` to make hook upload errors fail the hook   |

### JSON config file

Instead of environment variables you can create `~/.claude/langfuse.json` (global) or `<project>/.claude/langfuse.json` (per-project):

```json
{
  "public_key": "pk-lf-...",
  "secret_key": "sk-lf-...",
  "base_url": "https://us.cloud.langfuse.com"
}
```

### Data regions

| Region   | `LANGFUSE_BASE_URL`                |
| -------- | ---------------------------------- |
| 🇺🇸 US    | `https://us.cloud.langfuse.com`    |
| 🇪🇺 EU    | `https://cloud.langfuse.com`       |
| 🇯🇵 Japan | `https://jp.cloud.langfuse.com`    |
| ⚕️ HIPAA | `https://hipaa.cloud.langfuse.com` |

## How it works

A `Stop` hook runs `bun "${CLAUDE_PLUGIN_ROOT}/dist/index.mjs"` after every turn (and again on `SessionEnd`, to catch anything the final `Stop` missed). The hook reads the session transcript **incrementally**: a small sidecar file next to the transcript (`<transcript>.jsonl.langfuse`) records the byte offset already processed and the number of turns emitted, so each turn is uploaded exactly once even though the hook fires repeatedly over the life of a session.

Spans are sent via the [Langfuse TypeScript SDK](https://langfuse.com/docs/sdk/typescript) on top of OpenTelemetry, batched, and flushed once at the end of the hook (capped by the hook's 30s timeout). The hook **fails open**: any error is swallowed (logged in debug mode) so a tracing problem never blocks your Claude Code session.

## Development

The hook ships as a committed, pre-bundled `dist/index.mjs` so it runs without an install step. To work on it:

```bash
bun install
bun run build        # bundle src/ → dist/index.mjs (bun build)
bun run test         # bun test
bun run lint         # prettier + tsc --noEmit + verify dist is up to date
```

All runtime dependencies (Langfuse SDK, OpenTelemetry, zod) are bundled into the single `dist/index.mjs`; only Node.js built-ins (which Bun provides) stay external. Rebuild and commit `dist/index.mjs` after any change to `src/`.

## Troubleshooting

- **Nothing appears in Langfuse** — run with `CC_LANGFUSE_DEBUG=true` to log to stderr; confirm with `claude plugin list` that the plugin is enabled and that you restarted Claude Code.
- **Authentication fails** — check that the public/secret keys are valid and that `LANGFUSE_BASE_URL` matches the region the keys belong to (US is the default here).
- **Traces land in the wrong project** — API keys are project-scoped in Langfuse; use the keys for the project you want.
- **Testing hook failures** — set `CC_LANGFUSE_FAIL_ON_ERROR=true` together with `CC_LANGFUSE_DEBUG=true` to surface upload or flush errors instead of failing open.
- **Re-uploading a session** — delete the `<transcript>.jsonl.langfuse` sidecar to reprocess the transcript from the start.
- **Self-hosting** — the TypeScript SDK requires Langfuse platform version >= 3.95.0.

## Data sent to Langfuse

When enabled, the plugin uploads transcript data to Langfuse: prompts, assistant messages, tool-call inputs and outputs, model metadata, and token usage. Do not enable tracing for sessions containing data you do not want stored in Langfuse. Use `CC_LANGFUSE_MAX_CHARS` to cap how much of large inputs/outputs is captured.

## License

MIT
