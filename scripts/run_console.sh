#!/usr/bin/env bash
# The console alone, against the HOSTED lab (WS13).
#
#   scripts/run_console.sh            # http://localhost:8200
#
# WHY THIS EXISTS. `scripts/run_local.sh` starts thirteen processes because it
# predates hosting: eleven protocol faces, the bridge and the console. Every one
# of those except the console now runs on Fargate, so starting them locally
# rebuilds an entire lab in order to iterate on one web app.
#
# With `A2ALAB_MODE=hosted` in .env — which is the normal setting now — the
# console resolves every target to its HOSTED twin. So the console alone is a
# complete environment: Run buttons reach the real Fargate faces and the real
# AgentCore runtimes, Observability reads the real Aurora store, and the Lab
# Guide answers from the repo prose on disk. Edit, restart, refresh.
#
# WHEN YOU STILL WANT run_local.sh: you are changing an ADAPTER rather than the
# console — `src/platforms/**`, `src/interop/servers/**`, the delegation guard —
# and want to exercise it before it is deployed. Those are the faces' own code,
# and only run_local.sh runs them on this machine.
#
# WHAT THIS DOES NOT DO. It does not touch AWS. Publishing is a separate,
# deliberate step:
#
#     deploy/console/deploy_console.sh                # code change (rebuilds)
#     deploy/console/deploy_console.sh --skip-build   # config/credential only
#
# A code change deployed with --skip-build ships the OLD image (see CLAUDE.md).
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
export PYTHONPATH=src

PORT="${CONSOLE_PORT:-8200}"

# Reclaim the port the same way run_local.sh does — a half-dead uvicorn holding
# :8200 is the most common reason "my change did nothing".
if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
  echo "port $PORT busy — stopping what is there"
  lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
  sleep 1
fi

if [ "${A2ALAB_MODE:-local}" != "hosted" ]; then
  cat >&2 <<'WARN'
warning: A2ALAB_MODE is not "hosted".

  The console will resolve targets to localhost:80xx — the protocol faces,
  which this script does NOT start. Run buttons will fail with connection
  errors that look like the hosted lab being down.

  Set A2ALAB_MODE=hosted in .env, or use scripts/run_local.sh instead.
WARN
fi

echo "console      http://localhost:$PORT"
echo "mode         ${A2ALAB_MODE:-local}  (targets resolve to the hosted twins)"
echo "faces        ${A2ALAB_FACES_BASE:-<unset — Run buttons will fail>}"
echo "obs store    ${A2ALAB_OBS_STORE:-sqlite}"
# Unset here on purpose: with no harvest function the Harvest button sweeps
# IN-PROCESS, which is the local behaviour (D54) and avoids a dev console
# firing the production harvest Lambda by accident. Export it before running
# this script if you specifically want to test the hosted path.
echo "harvest      ${A2ALAB_HARVEST_FUNCTION:+hosted Lambda ($A2ALAB_HARVEST_FUNCTION)}${A2ALAB_HARVEST_FUNCTION:-in-process (local)}"
echo
exec uv run python -m console --port "$PORT"
