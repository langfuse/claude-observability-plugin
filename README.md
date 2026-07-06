# Langfuse Observability Plugin for Claude Code

Trace every Claude Code session to [Langfuse](https://langfuse.com) — turns, generations, tool calls, and token usage — with zero code changes.

## Install

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

Restart Claude Code after install.

On enable, you'll be prompted for:

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

Get keys from your Langfuse project settings → API Keys.

## Requirements

One of:

- [uv](https://docs.astral.sh/uv/) (recommended) — installs the langfuse SDK automatically, no setup needed.
- Python 3.10+ as `python3` with `pip install "langfuse>=4.0,<5"` (fallback when uv is not on PATH).

If neither is set up, the hook exits silently — no impact on Claude Code.

## How it works

A hook reads the session transcript incrementally on every turn (Stop) and at session end (SessionEnd), and emits a Langfuse trace with one span per turn, nested generations per assistant message, and child tool spans for every tool call. Token usage is captured when present.

**Subagents (Task tool)** run in isolated sub-sessions with their own transcripts under
`<session>/subagents/agent-*.jsonl`, so their model calls, tool use and cost are absent from the
parent transcript. The hook sweeps that directory and emits each subagent as a separate trace in
the **same Langfuse session**, tagged `subagent` / `agent:<type>` / `parent_tool:<id>` and linked
back to the spawning `Task` tool_use via metadata (`parent_tool_use_id`). Nested subagents
(`spawnDepth > 1`) are handled recursively. Without this, only the orchestrator model appears and
session cost is undercounted.

State is kept in `~/.claude/state/langfuse_state.json` so re-runs only emit new turns (per-file
offsets, including one per subagent transcript).

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

```bash
claude plugin disable langfuse-observability
claude plugin enable langfuse-observability
```

## Uninstall

```bash
claude plugin uninstall langfuse-observability
```

## Troubleshooting

- Nothing in Langfuse: check `~/.claude/state/langfuse_hook.log` (enable `CC_LANGFUSE_DEBUG`).
- Hook not firing: confirm with `claude plugin list` that langfuse-observability is enabled; restart Claude Code.
- langfuse import errors (no uv): ensure the `python3` on your PATH has the SDK installed, or install uv.

## License

MIT
