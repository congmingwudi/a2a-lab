#!/usr/bin/env bash
# Build, push, and create-or-update a Bedrock AgentCore runtime for a lab
# platform (D26 — the scripted deploy M9 did by hand).
#
#   deploy/agentcore/deploy.sh claude          # deploy/update a2alab_claude
#   deploy/agentcore/deploy.sh openai          # deploy/update a2alab_openai
#   deploy/agentcore/deploy.sh claude --skip-build   # redeploy current image
#
# Requires: .env populated (AWS_PROFILE/AWS_REGION + the platform's keys),
# an authenticated AWS session (aws sso login --profile embark), and Docker.
# AgentCore Runtime requires linux/arm64 images.
#
# Role: reuses AGENTCORE_ROLE_ARN if set, else copies the execution role off
# an already-deployed lab runtime (the M9 openai runtime bootstrapped this).
#
# On success prints the runtime ARN and writes it back to .env
# (CLAUDE_AGENTCORE_ARN / OPENAI_AGENTCORE_ARN).
set -euo pipefail
cd "$(dirname "$0")/../.."

PLATFORM="${1:?usage: deploy.sh <claude|openai> [--skip-build]}"
SKIP_BUILD="${2:-}"

set -a; source .env; set +a
REGION="${AWS_REGION:-us-east-1}"

case "$PLATFORM" in
  claude)
    DOCKERFILE=deploy/agentcore/Dockerfile
    RUNTIME_NAME=a2alab_claude
    ARN_VAR=CLAUDE_AGENTCORE_ARN
    # SF_AGENT_ID: the Claude-paired Agentforce twin (D25 — closed systems)
    ENV_KEYS=(ANTHROPIC_API_KEY CLAUDE_AGENT_MODEL CLAUDE_ANSWER_TIMEOUT_S
              SF_MY_DOMAIN SF_CLIENT_ID SF_CLIENT_SECRET SF_AGENT_ID
              AF_SHIM_A2A_URL AF_SHIM_TIMEOUT_S
              A2ALAB_PG_CLUSTER_ARN A2ALAB_PG_SECRET_ARN)
    ;;
  openai)
    DOCKERFILE=deploy/agentcore/openai.Dockerfile
    RUNTIME_NAME=a2alab_openai
    ARN_VAR=OPENAI_AGENTCORE_ARN
    # SF_OPENAI_AGENT_ID: the OpenAI-paired Agentforce twin (D25)
    ENV_KEYS=(OPENAI_API_KEY OPENAI_MODEL OPENAI_ANSWER_TIMEOUT_S
              SF_MY_DOMAIN SF_CLIENT_ID SF_CLIENT_SECRET SF_OPENAI_AGENT_ID
              AF_SHIM_A2A_URL AF_SHIM_TIMEOUT_S
              A2ALAB_PG_CLUSTER_ARN A2ALAB_PG_SECRET_ARN)
    ;;
  *) echo "unknown platform '$PLATFORM' (claude|openai)"; exit 1 ;;
esac

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_URI="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/a2alab-$PLATFORM"

# ---- image -----------------------------------------------------------------
if [ "$SKIP_BUILD" != "--skip-build" ]; then
  aws ecr describe-repositories --repository-names "a2alab-$PLATFORM" >/dev/null 2>&1 \
    || aws ecr create-repository --repository-name "a2alab-$PLATFORM" >/dev/null
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
  docker buildx build --platform linux/arm64 -f "$DOCKERFILE" -t "$ECR_URI:latest" --push .
fi

# ---- execution role --------------------------------------------------------
ROLE_ARN="${AGENTCORE_ROLE_ARN:-}"
if [ -z "$ROLE_ARN" ]; then
  # The list API returns summaries without roleArn — find any lab runtime,
  # then read its role off get-agent-runtime.
  DONOR_ID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
    --query "agentRuntimes[?starts_with(agentRuntimeName, 'a2alab_')].agentRuntimeId | [0]" \
    --output text)
  if [ -n "$DONOR_ID" ] && [ "$DONOR_ID" != "None" ]; then
    ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --region "$REGION" \
      --agent-runtime-id "$DONOR_ID" --query roleArn --output text)
  fi
  if [ -z "$ROLE_ARN" ] || [ "$ROLE_ARN" = "None" ]; then
    echo "no AGENTCORE_ROLE_ARN set and no existing a2alab_* runtime to copy a role from" >&2
    exit 1
  fi
fi

# ---- runtime env vars (only keys that are set locally) ---------------------
# A2ALAB_TRACE_SINK=postgres: the container writes hops to the Aurora store
# (local dev uses the default jsonl sink; the runtime has no local traces/).
# The container gets the WRITER secret: local .env carries the reader (for
# console queries), but the Data API secret IS the role selection (D23) and
# a runtime inserting hops through the reader fails read-only.
ENV_JSON=$(python3 - "${ENV_KEYS[@]}" <<'PY'
import json, os, sys
env = {k: os.environ[k] for k in sys.argv[1:] if os.environ.get(k)}
if env.get("A2ALAB_PG_CLUSTER_ARN"):
    env["A2ALAB_TRACE_SINK"] = "postgres"
    writer = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
    if writer:
        env["A2ALAB_PG_SECRET_ARN"] = writer
# The shim credential rides as AF_SHIM_TOKEN, never A2ALAB_TOKEN: setting
# A2ALAB_TOKEN in the runtime flips on the container's own inbound bearer
# auth, which invoke_agent_runtime cannot satisfy — every invoke 401s.
if os.environ.get("A2ALAB_TOKEN"):
    env["AF_SHIM_TOKEN"] = os.environ["A2ALAB_TOKEN"]
print(json.dumps(env))
PY
)

# ---- create or update ------------------------------------------------------
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?agentRuntimeName=='$RUNTIME_NAME'].agentRuntimeId | [0]" --output text)

if [ -z "$RUNTIME_ID" ] || [ "$RUNTIME_ID" = "None" ]; then
  ARN=$(aws bedrock-agentcore-control create-agent-runtime --region "$REGION" \
    --agent-runtime-name "$RUNTIME_NAME" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$ECR_URI:latest\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --environment-variables "$ENV_JSON" \
    --query agentRuntimeArn --output text)
  echo "created $RUNTIME_NAME -> $ARN"
else
  ARN=$(aws bedrock-agentcore-control update-agent-runtime --region "$REGION" \
    --agent-runtime-id "$RUNTIME_ID" \
    --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"$ECR_URI:latest\"}}" \
    --role-arn "$ROLE_ARN" \
    --network-configuration '{"networkMode":"PUBLIC"}' \
    --protocol-configuration '{"serverProtocol":"HTTP"}' \
    --environment-variables "$ENV_JSON" \
    --query agentRuntimeArn --output text)
  echo "updated $RUNTIME_NAME ($RUNTIME_ID) -> $ARN"
fi

# ---- write the ARN back to .env -------------------------------------------
python3 - "$ARN_VAR" "$ARN" <<'PY'
import sys
var, arn = sys.argv[1], sys.argv[2]
lines = open(".env").read().splitlines()
hit = False
for i, ln in enumerate(lines):
    if ln.startswith(f"{var}="):
        lines[i] = f"{var}={arn}"
        hit = True
if not hit:
    lines.append(f"{var}={arn}")
open(".env", "w").write("\n".join(lines) + "\n")
print(f".env: {var} set")
PY

echo "smoke test: uv run python scripts/matrix.py ${RUNTIME_NAME/a2alab_/}-agentcore --runs 1 --no-record"
