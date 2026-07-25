#!/usr/bin/env bash
# Start the full local lab stack (Claude agent on all three protocols, the
# OpenAI agent, the Lab Guide, the Agentforce shims, the bridge, the console
# and the brief watcher). Ctrl-C stops everything.
#
# Why the port bookkeeping below: every server is launched as `uv run python
# -m ...`, so the process that actually holds the listening socket is uv's
# CHILD. Killing the recorded pid (or Ctrl-C'ing a stack whose parent shell
# has already gone) can leave that child alive and the port bound — which
# surfaces on the next start as
#   ERROR: [Errno 48] error while attempting to bind on address ... in use
# and, worse, as a stack that silently keeps running last week's code. So the
# stack is reclaimed BY PORT on the way in and on the way out.
set -euo pipefail
cd "$(dirname "$0")/.."

# The stack's own skip-checks read credentials from the environment
# (SF_CLIENT_ID gates the Agentforce shims), so source .env here — that makes
# `scripts/run_local.sh` self-sufficient, the way CLAUDE.md documents it.
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

export PYTHONPATH=src

# Every port this stack listens on. Keep in sync with the run lines below —
# this list is the cleanup contract in both directions.
PORTS=(8001 8002 8003 8011 8012 8013 8021 8023 8031 8032 8033 8100 8200)
# The brief watcher binds nothing, so it can only be matched by name.
WATCHER_PATTERN="python -m briefs --watch"

_listeners_on() { lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null || true; }

_ports_busy() {
  local port
  for port in "${PORTS[@]}"; do
    if [[ -n "$(_listeners_on "$port")" ]]; then return 0; fi
  done
  return 1
}

# mode=preflight -> narrate, and refuse to kill anything that isn't ours.
# mode=shutdown  -> quiet, best effort, we own everything by then.
reclaim_ports() {
  local mode="$1" port pid cmd
  for port in "${PORTS[@]}"; do
    for pid in $(_listeners_on "$port"); do
      cmd=$(ps -o command= -p "$pid" 2>/dev/null || true)
      if [[ "$mode" == "preflight" && ! "$cmd" =~ (python|uv) ]]; then
        echo "!! :$port is held by a non-lab process (pid $pid): ${cmd:0:90}" >&2
        echo "   refusing to kill it — free that port yourself and re-run." >&2
        exit 1
      fi
      if [[ "$mode" == "preflight" ]]; then
        echo "  reclaiming :$port from pid $pid"
      fi
      kill "$pid" 2>/dev/null || true
    done
  done
  pkill -f "$WATCHER_PATTERN" 2>/dev/null || true

  # Ports linger for a moment after SIGTERM; insist only if they don't clear.
  local i
  for i in $(seq 1 12); do
    if ! _ports_busy; then return 0; fi
    sleep 0.25
  done
  for port in "${PORTS[@]}"; do
    for pid in $(_listeners_on "$port"); do
      if [[ "$mode" == "preflight" ]]; then
        echo "  :$port did not release — SIGKILL pid $pid"
      fi
      kill -9 "$pid" 2>/dev/null || true
    done
  done
  sleep 0.5
}

PIDS=()
cleanup() {
  kill "${PIDS[@]}" 2>/dev/null || true
  reclaim_ports shutdown
}
trap cleanup EXIT

echo "checking for a previous stack..."
reclaim_ports preflight
echo "ports clear."
echo

run() { echo "+ $*"; "$@" & PIDS+=($!); }

run uv run python -m platforms.claude --protocol rest --port 8001
run uv run python -m platforms.claude --protocol mcp  --port 8002
run uv run python -m platforms.claude --protocol a2a  --port 8003
# OpenAI agent (M9/D24) — backend from OPENAI_BACKEND (.env; stub if unset)
run uv run python -m platforms.openai --protocol rest --port 8011
run uv run python -m platforms.openai --protocol mcp  --port 8012
run uv run python -m platforms.openai --protocol a2a  --port 8013
if [[ -n "${SF_CLIENT_ID:-}" ]]; then
  run uv run python -m platforms.agentforce.mcp_shim --port 8021
  run uv run python -m platforms.agentforce.a2a_shim --port 8023
else
  echo "(Agentforce shims skipped — SF_CLIENT_ID not set)"
fi
# Lab Guide (plan/07) — the console docent, served as a lab agent over all
# three protocols (the meta exhibit; MCP additionally exposes raw read tools)
run uv run python -m platforms.guide --protocol rest --port 8031
run uv run python -m platforms.guide --protocol mcp  --port 8032
run uv run python -m platforms.guide --protocol a2a  --port 8033
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

# Readiness probe: a bind failure otherwise scrolls past in the noise of a
# dozen starting servers, leaving a stack that is missing exactly one cell.
echo
echo "waiting for the stack to bind..."
EXPECTED=()
for port in "${PORTS[@]}"; do
  # the shims are only started when Salesforce credentials are present
  if [[ -z "${SF_CLIENT_ID:-}" && ( "$port" == "8021" || "$port" == "8023" ) ]]; then continue; fi
  EXPECTED+=("$port")
done
missing=()
for _ in $(seq 1 40); do
  missing=()
  for port in "${EXPECTED[@]}"; do
    if [[ -z "$(_listeners_on "$port")" ]]; then missing+=("$port"); fi
  done
  if [[ ${#missing[@]} -eq 0 ]]; then break; fi
  sleep 0.5
done
if [[ ${#missing[@]} -eq 0 ]]; then
  echo "all ${#EXPECTED[@]} ports listening."
else
  echo "!! not listening after 20s: ${missing[*]} — scroll up for that server's error." >&2
fi

echo
echo "lab console:   http://localhost:8200"
echo "bridge:        http://localhost:8100/invoke/{target}"
echo "matrix:        uv run python scripts/matrix.py"
wait
