# Development and Maintenance

See the [main CONTRIBUTING.md](https://github.com/langfuse/langfuse/blob/main/CONTRIBUTING.md) to learn how to contribute to Langfuse and its integrations.

## Local Development

The hook is a single uv script with inline dependency metadata, and Claude Code
runs `hooks/langfuse_hook.py` directly. There is no build step and no package to
install.

Run the tests:

```bash
uv run --group dev pytest
```

Lint with the same rules as CI:

```bash
uvx ruff check --select F,E9 .
```

CI also starts the hook once with an empty payload, which catches a broken
script header before it reaches a user:

```bash
echo '{}' | uv run --script hooks/langfuse_hook.py
```

Tests run against Python 3.10 and 3.13 in CI. Add `--python 3.10` to `uv run` to
reproduce the older matrix entry.

To try your checkout as a real plugin, register the repository itself as a
marketplace instead of the GitHub one:

```bash
claude plugin marketplace add /path/to/Claude-Observability-Plugin
claude plugin install langfuse-observability@langfuse-observability
```

Restart Claude Code afterwards, so that it loads the hook. Run
`claude plugin marketplace remove langfuse-observability` when you are done, and
add the GitHub marketplace again to go back to the released plugin.

## Releasing

1. From a clean, up-to-date `main` branch, raise `version` in
   `.claude-plugin/plugin.json` and commit that change.

2. Tag the release commit and push both:

   ```bash
   git tag v1.2.3
   git push origin main --follow-tags
   ```

   The release workflow refuses a tag that does not match the manifest version.
   It then runs the tests and the hook smoke check, and creates a draft GitHub
   release with generated notes.

3. Review the draft release, give the notes a human pass, and publish it.

Installed plugins do not update themselves, so users reach the new version only
after `claude plugin marketplace update` and `claude plugin update`.
