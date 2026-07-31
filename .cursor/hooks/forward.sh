#!/usr/bin/env bash
# Project hook entry: auto-start cursorscope, forward Cursor Agent events to OTLP.
set -euo pipefail

export CURSORSCOPE_HOME="${CURSORSCOPE_HOME:-$HOME/.cursorscope}"

if [ ! -x "$CURSORSCOPE_HOME/scripts/cursorscope-forward.sh" ]; then
  # First run before `scripts/cursor_otel.sh` — degrade silently, never block Agent.
  exit 0
fi

exec "$CURSORSCOPE_HOME/scripts/cursorscope-forward.sh"
