# Langfuse Observability Plugin for Claude Code

Claude Code plugin that sends [Claude Code](https://claude.com/claude-code)
session telemetry to [Langfuse](https://langfuse.com). It traces user prompts,
agent turns, model generations with their token usage and cost, thinking blocks,
tool calls, skills, subagents, and pasted or returned images.

Langfuse also documents this integration on the
[Claude Code integration page](https://langfuse.com/integrations/developer-tools/claude-code).

## Quick Start

Add the marketplace and install the plugin:

```bash
claude plugin marketplace add langfuse/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

Restart Claude Code after installing, so that it loads the hook.

Tracing covers the `claude` CLI and the desktop app in **Code** mode. Claude
Desktop **Chat** mode runs no Claude Code hooks and is not traced.

## Prerequisites

The plugin needs [uv](https://docs.astral.sh/uv/) on `PATH`. The hook runs as a
uv script and installs the Langfuse SDK from its own inline script metadata.
Machines without uv fall back to Python 3.10+ as `python3` with
`langfuse>=4.7,<5` installed.

Without a usable runtime the hook writes the reason to its log and exits. It
never blocks or slows Claude Code.

## Langfuse Credentials

Configure the plugin from inside a Claude Code session. This is a Claude Code
slash command, not a shell command:

```text
/plugin configure langfuse-observability@langfuse-observability
```

You can also pass the values during install:

```bash
claude plugin install langfuse-observability@langfuse-observability \
  --config LANGFUSE_PUBLIC_KEY=pk-lf-... \
  --config LANGFUSE_SECRET_KEY=sk-lf-... \
  --config LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

Only `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are required. Without
`LANGFUSE_BASE_URL` the plugin uses `https://cloud.langfuse.com` (EU region).
Get the keys from the API Keys page in your Langfuse project settings. The
secret key is held in your OS keychain, not in a file.

`/plugin configure` also lists the optional settings, such as the user ID,
debug logging, and image capture, with their defaults.

## Updating

Automatic updates are off for marketplaces outside Anthropic's, so you run the
update yourself.

```bash
claude plugin marketplace update langfuse-observability
claude plugin update langfuse-observability@langfuse-observability
```

Restart Claude Code afterwards. `claude plugin list` shows the installed
version.

## Troubleshooting

The hook explains nearly every failure in
`~/.claude/state/langfuse_hook.log`. Send one message, then read the newest
lines. Turn on `CC_LANGFUSE_DEBUG` for verbose logging.

A log with no new lines means that the hook never started. Either the plugin is
disabled, or no usable runtime was found. Run `claude plugin list` from the
directory you work in, and put uv on the `PATH` of the app that starts Claude
Code.

## Contributing

See the [contributing guide](./CONTRIBUTING.md).

## License

[MIT](./LICENSE)
