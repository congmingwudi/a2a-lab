#!/usr/bin/env bash
# Deploy the LangGraph agent (WS4) to Heroku — HEADLESS, no `heroku` CLI login.
#
# Everything here is the Heroku Platform API (https://api.heroku.com) + the
# Container Registry over plain docker + curl, authenticated by an API token:
# app create, config vars, image push, and release. The one step only the
# operator can do is minting that token (heroku authorizations:create, or the
# Account-settings API key) — see plan/07 WS4 "Credentials / setup".
#
# No environment identifier is hardcoded (CLAUDE.md): the app name, team, and
# token all come from .env and fail loudly if unset.
#
#   HEROKU_APP       target app name           (e.g. a2a-lab-langgraph)
#   HEROKU_TEAM      team the app lives in      (e.g. sfdc-ta)
#   HEROKU_API_KEY   API token with team access (secret; synced via env_sync)
#   HEROKU_SPACE     optional: Private Space name (Heroku Enterprise only)
#
# Usage:
#   deploy/heroku/deploy_langgraph.sh            # full: build, push, release
#   deploy/heroku/deploy_langgraph.sh --skip-build  # config-vars + release only
#
# NOTE the --skip-build caveat (mirrors the Fargate scripts): it re-releases the
# CURRENT image with refreshed config vars. Anything touching src/ or config/
# needs a full build — those are baked in by COPY.
set -euo pipefail

APP="${HEROKU_APP:?set HEROKU_APP in .env}"
TEAM="${HEROKU_TEAM:?set HEROKU_TEAM in .env}"
SPACE="${HEROKU_SPACE:-}"

# Token resolution: an explicit HEROKU_API_KEY wins; otherwise fall back to the
# current `heroku` CLI session token. Under Heroku Enterprise SSO, long-lived
# personal tokens are usually disabled and `heroku auth:token` returns a
# SHORT-LIVED session token — fine for a one-shot deploy, not for persisting,
# which is why we do not write it to .env.
TOKEN="${HEROKU_API_KEY:-}"
if [[ -z "$TOKEN" ]] && command -v heroku >/dev/null 2>&1; then
  TOKEN="$(heroku auth:token 2>/dev/null || true)"
fi
: "${TOKEN:?no token — set HEROKU_API_KEY in .env or run \`heroku login\` first}"

API="https://api.heroku.com"
ACCEPT="Accept: application/vnd.heroku+json; version=3"
AUTH="Authorization: Bearer ${TOKEN}"
REGISTRY="registry.heroku.com/${APP}/web"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

SKIP_BUILD=0
[[ "${1:-}" == "--skip-build" ]] && SKIP_BUILD=1

hapi() {  # method path [json-body]
  local method="$1" path="$2" body="${3:-}"
  if [[ -n "$body" ]]; then
    curl -fsS -X "$method" "${API}${path}" -H "$ACCEPT" -H "$AUTH" \
      -H "Content-Type: application/json" -d "$body"
  else
    curl -fsS -X "$method" "${API}${path}" -H "$ACCEPT" -H "$AUTH"
  fi
}

echo "==> Ensuring app '${APP}' exists in team '${TEAM}'"
if hapi GET "/apps/${APP}" >/dev/null 2>&1; then
  echo "    app exists"
else
  echo "    creating app in team ${TEAM}${SPACE:+ (space ${SPACE})}"
  body="{\"name\":\"${APP}\",\"team\":\"${TEAM}\"$([[ -n "$SPACE" ]] && echo ",\"space\":\"${SPACE}\"")}"
  hapi POST "/teams/apps" "$body" >/dev/null
fi

echo "==> Setting config vars (only those present in the environment)"
# Curated allowlist: the agent brain, the lab token, the paired-Agentforce
# creds for ask_agentforce, the trace sink + Data-API + AWS creds, and the
# public origin the A2A card advertises. Values come from the current env
# (populate .env; run_console.sh / your shell already load it).
VARS=(
  LANGGRAPH_BACKEND LANGGRAPH_MODEL_ID LANGGRAPH_ANSWER_TIMEOUT_S
  ANTHROPIC_API_KEY A2ALAB_TOKEN A2ALAB_MAX_DELEGATION_DEPTH
  A2ALAB_LANGGRAPH_BASE
  SF_MY_DOMAIN SF_CLIENT_ID SF_CLIENT_SECRET SF_LANGGRAPH_AGENT_ID
  A2ALAB_TRACE_SINK A2ALAB_PG_CLUSTER_ARN A2ALAB_PG_SECRET_ARN A2ALAB_PG_DSN
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_REGION
  LANGSMITH_API_KEY LANGCHAIN_TRACING_V2 LANGCHAIN_PROJECT
)
payload="{"
first=1
for v in "${VARS[@]}"; do
  val="${!v:-}"
  [[ -z "$val" ]] && continue
  esc="${val//\\/\\\\}"; esc="${esc//\"/\\\"}"
  [[ $first -eq 0 ]] && payload+=","
  payload+="\"${v}\":\"${esc}\""
  first=0
done
payload+="}"
hapi PATCH "/apps/${APP}/config-vars" "$payload" >/dev/null
echo "    set: $(echo "$payload" | tr ',' '\n' | grep -oE '"[A-Z_]+":' | tr -d '":' | tr '\n' ' ')"

if [[ $SKIP_BUILD -eq 0 ]]; then
  echo "==> Building + pushing image to Heroku Container Registry"
  echo "$TOKEN" | docker login registry.heroku.com -u _ --password-stdin
  # linux/amd64: Heroku runs amd64 dynos; build explicitly so an arm64 laptop
  # does not push an unrunnable image (the same trap the Fargate arm64 build
  # avoids in the other direction).
  #
  # oci-mediatypes=false + provenance=false: the Heroku Container Registry
  # rejects OCI-format manifests with "error from registry: unsupported".
  # Docker 29's containerd image store keeps images in OCI media types, so a
  # plain `docker build` + `docker push` pushes an OCI manifest and fails.
  # buildx's image exporter with oci-mediatypes=false forces a Docker schema2
  # manifest (what Heroku accepts) and pushes it directly (push=true), so there
  # is no separate `docker push` and no reliance on the local daemon format.
  docker buildx build --platform linux/amd64 --provenance=false \
    --output "type=image,name=${REGISTRY},oci-mediatypes=false,push=true" \
    -f "${ROOT}/deploy/heroku/Dockerfile" "$ROOT"
else
  echo "==> --skip-build: releasing the CURRENT image with refreshed config"
fi

echo "==> Releasing"
# Heroku's formation `docker_image` is the image CONFIG digest (what
# `docker inspect --format '{{.Id}}'` returns for a locally-loaded image). With
# push=true the image is not in the local daemon, so read the config digest
# from the pushed manifest instead.
IMAGE_ID="$(docker buildx imagetools inspect "$REGISTRY" --raw 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["config"]["digest"])' 2>/dev/null || true)"
if [[ -z "$IMAGE_ID" ]]; then
  echo "    could not resolve pushed image config digest (build first?)" >&2
  exit 1
fi
echo "    releasing image ${IMAGE_ID}"
hapi PATCH "/apps/${APP}/formation" \
  "{\"updates\":[{\"type\":\"web\",\"docker_image\":\"${IMAGE_ID}\"}]}" \
  >/dev/null 2>&1 || \
  curl -fsS -X PATCH "${API}/apps/${APP}/formation" \
    -H "Accept: application/vnd.heroku+json; version=3.docker-releases" \
    -H "$AUTH" -H "Content-Type: application/json" \
    -d "{\"updates\":[{\"type\":\"web\",\"docker_image\":\"${IMAGE_ID}\"}]}" >/dev/null

echo "==> Done. https://${APP}.herokuapp.com/  (set A2ALAB_LANGGRAPH_BASE to it)"
echo "    Then uncomment the langgraph-*-hosted twins + remap in config/targets.yaml."
