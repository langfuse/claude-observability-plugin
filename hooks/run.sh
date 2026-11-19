#!/bin/sh
# Starts langfuse_hook.py with the first runner that can start.
#
# 1. The user's uv as configured, so index, TLS and Python settings in their
#    uv.toml still apply.
# 2. The same uv without any uv config (--no-config) and without
#    UV_PREVIEW_FEATURES, for when the uv on PATH cannot load those settings.
#    This happens when a project pins an older uv (proto, mise, asdf) than the
#    one the user's global uv config was written for. The script declares its
#    dependencies inline, so it needs no uv config.
# 3. python3, which logs a hint and exits 0 if langfuse is not importable.
#
# Each probe runs before the hook starts, so the hook runs at most once. The
# probes read no stdin, leaving the hook payload for the hook. `uv cache dir`
# loads the same user and project config as `uv run`; tool commands such as
# `uv tool dir` skip project config, so they can fail where `uv run` works.

HOOK="$(dirname "$0")/langfuse_hook.py"

if command -v uv >/dev/null 2>&1; then
  if uv cache dir </dev/null >/dev/null 2>&1; then
    exec uv run --quiet --script "$HOOK"
  fi
  if env -u UV_PREVIEW_FEATURES uv --no-config cache dir </dev/null >/dev/null 2>&1; then
    exec env -u UV_PREVIEW_FEATURES uv run --no-config --quiet --script "$HOOK"
  fi
fi

exec python3 "$HOOK"
