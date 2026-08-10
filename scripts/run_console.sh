#!/usr/bin/env bash
# The console alone, against the HOSTED lab (WS13).
#
#   scripts/run_console.sh            # http://localhost:8200
#
# WHY THIS EXISTS. `scripts/run_local.sh` starts sixteen processes because it
# predates hosting: fourteen protocol faces, the bridge and the console. Every one
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

# --reload: uvicorn watches src/ and restarts on a Python change. Without it,
# editing app.py and refreshing the browser shows the OLD code — index.html is
# read from disk per request, so HTML edits appear instantly and Python edits do
# not, which is a genuinely confusing pair of behaviours to hold in your head.
# It cost a debugging round on a harvest 500 that was simply a stale process.
RELOAD="${CONSOLE_RELOAD:-1}"

echo "console      http://localhost:$PORT"
echo "mode         ${A2ALAB_MODE:-local}  (targets resolve to the hosted twins)"
echo "faces        ${A2ALAB_FACES_BASE:-<unset — Run buttons will fail>}"
echo "obs store    ${A2ALAB_OBS_STORE:-sqlite}"
# The Harvest button fires the harvest Lambda from here too (D54), the same as
# hosted. The in-process sweep is NOT a working local fallback — it writes, and
# .env points A2ALAB_PG_SECRET_ARN at the READER secret, so every source raises
# "cannot execute INSERT in a read-only transaction". Set
# A2ALAB_HARVEST_FUNCTION="" to force it anyway.
if [ -n "${A2ALAB_HARVEST_FUNCTION-unset}" ] && [ "${A2ALAB_HARVEST_FUNCTION-a2alab-obs-harvest}" != "" ]; then
  echo "harvest      fires ${A2ALAB_HARVEST_FUNCTION:-a2alab-obs-harvest} (same as hosted)"
else
  echo "harvest      in-process (forced; writes need the WRITER secret)"
fi
if [ "$RELOAD" = "1" ]; then
  echo "reload       on — Python changes restart the server automatically"
else
  echo "reload       off"
fi
echo

if [ "$RELOAD" = "1" ]; then
  # --factory calls create_console_app() with no args. It skips console.main(),
  # which is fine HERE and only here: main() does load_dotenv (this script has
  # already sourced .env), the Secrets Manager load (a no-op with no runtime
  # secret ARN) and the hosted fail-closed guard (hosted only).
  exec uv run uvicorn console.app:create_console_app --factory \
    --host 0.0.0.0 --port "$PORT" --reload --reload-dir src
fi
exec uv run python -m console --port "$PORT"
