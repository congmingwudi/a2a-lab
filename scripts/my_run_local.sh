#!/usr/bin/env bash
# Ryan's entry point for the local stack.
#
# run_local.sh now sources .env itself and reclaims stale ports before it
# starts (a `uv run` child can outlive its parent and keep a port bound —
# that's the Errno 48 you get on the next start), so this wrapper is just
# the reminder of where the app is served.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "console (public): https://console-lab.agenticthings.com/?token=<A2ALAB_TOKEN from .env>"
echo

exec ./scripts/run_local.sh
