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

echo
echo "lab console:   http://localhost:8200"
echo "bridge:        http://localhost:8100/invoke/{target}"
echo "matrix:        uv run python scripts/matrix.py"
wait
