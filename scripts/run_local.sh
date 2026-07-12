#!/usr/bin/env bash
# Start the full local lab stack (Claude agent on all three protocols,
# Agentforce shims, bridge, console). Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONPATH=src
PIDS=()
cleanup() { kill "${PIDS[@]}" 2>/dev/null || true; }
trap cleanup EXIT

run() { echo "+ $*"; "$@" & PIDS+=($!); }

run uv run python -m platforms.claude --protocol rest --port 8001
run uv run python -m platforms.claude --protocol mcp  --port 8002
run uv run python -m platforms.claude --protocol a2a  --port 8003
if [[ -n "${SF_CLIENT_ID:-}" ]]; then
  run uv run python -m platforms.agentforce.mcp_shim --port 8021
  run uv run python -m platforms.agentforce.a2a_shim --port 8023
else
  echo "(Agentforce shims skipped — SF_CLIENT_ID not set)"
fi
run uv run python -m bridge --port 8100
run uv run python -m console --port 8200
if [[ -f .a2alab/brief.json && -n "${SF_CLIENT_ID:-}" ]]; then
  # Async brief pattern (D16): service sessions fired by the Anthropic
  # scheduled deployment (daily cron) — executes the Salesforce delivery
  # tool host-side. Sessions fired while this wasn't running just wait.
  run uv run python -m briefs --watch
else
  echo "(brief watcher skipped — run scripts/setup_brief_agent.py and set SF_* first)"
fi

echo
echo "lab console:   http://localhost:8200"
echo "bridge:        http://localhost:8100/invoke/{target}"
echo "matrix:        uv run python scripts/matrix.py"
wait
