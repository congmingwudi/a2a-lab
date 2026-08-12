#!/usr/bin/env bash
# Build, push, and create-or-update a Bedrock AgentCore runtime for a lab
# platform (D26 — the scripted deploy M9 did by hand).
#
#   deploy/agentcore/deploy.sh claude          # deploy/update a2alab_claude
#   deploy/agentcore/deploy.sh openai          # deploy/update a2alab_openai
#   deploy/agentcore/deploy.sh strands         # deploy/update a2alab_strands
#   deploy/agentcore/deploy.sh claude --skip-build   # redeploy current image
#
# Requires: .env populated (AWS_PROFILE/AWS_REGION + the platform's keys),
# an authenticated AWS session (aws sso login), and Docker.
# AgentCore Runtime requires linux/arm64 images.
#
# Role: reuses AGENTCORE_ROLE_ARN if set, else copies the execution role off
# an already-deployed lab runtime (the M9 openai runtime bootstrapped this).
#
# On success prints the runtime ARN and writes it back to .env
# (CLAUDE_AGENTCORE_ARN / OPENAI_AGENTCORE_ARN).
set -euo pipefail
cd "$(dirname "$0")/../.."

PLATFORM="${1:?usage: deploy.sh <claude|openai|strands> [--skip-build]}"
SKIP_BUILD="${2:-}"

set -a; source .env; set +a
source deploy/aws_preflight.sh
REGION="${AWS_REGION:-us-east-1}"

case "$PLATFORM" in
  claude)
    DOCKERFILE=deploy/agentcore/Dockerfile
    RUNTIME_NAME=a2alab_claude
    ARN_VAR=CLAUDE_AGENTCORE_ARN
    # SF_AGENT_ID: the Claude-paired Agentforce twin (D25 — closed systems)
    ENV_KEYS=(CLAUDE_AGENT_MODEL CLAUDE_ANSWER_TIMEOUT_S
              SF_MY_DOMAIN SF_AGENT_ID
              AF_SHIM_A2A_URL AF_SHIM_TIMEOUT_S
              A2ALAB_PG_CLUSTER_ARN A2ALAB_PG_SECRET_ARN)
    SECRET_KEYS=(ANTHROPIC_API_KEY SF_CLIENT_ID SF_CLIENT_SECRET)
    ;;
  openai)
    DOCKERFILE=deploy/agentcore/openai.Dockerfile
    RUNTIME_NAME=a2alab_openai
    ARN_VAR=OPENAI_AGENTCORE_ARN
    # SF_OPENAI_AGENT_ID: the OpenAI-paired Agentforce twin (D25).
    # SF_AGENT_ID must ship too: AgentforceClient.from_env() requires it
    # before the twin id overrides it (learned when a scripted redeploy
    # wiped it off the runtime and every hosted Agentforce consult broke).
    ENV_KEYS=(OPENAI_MODEL OPENAI_ANSWER_TIMEOUT_S
              SF_MY_DOMAIN SF_AGENT_ID SF_OPENAI_AGENT_ID
              AF_SHIM_A2A_URL AF_SHIM_TIMEOUT_S
              A2ALAB_PG_CLUSTER_ARN A2ALAB_PG_SECRET_ARN)
    SECRET_KEYS=(OPENAI_API_KEY SF_CLIENT_ID SF_CLIENT_SECRET)
    ;;
  strands)
    DOCKERFILE=deploy/agentcore/strands.Dockerfile
    RUNTIME_NAME=a2alab_strands
    ARN_VAR=STRANDS_AGENTCORE_ARN
    # WS5/D66: Strands is model-agnostic and runs on Amazon Bedrock — no model
    # API key. The runtime's IAM execution role calls bedrock:InvokeModel
    # (grant added below), so the secret carries no model credential — only the
    # Salesforce pair for the ask_agentforce tool, plus BRIDGE_TOKEN (SECRET_KEYS
    # below) for the cross-hyperscaler bridge route and AF_SHIM_TOKEN (injected
    # from A2ALAB_TOKEN). SF_AGENT_ID must ship too:
    # AgentforceClient.from_env() requires it before the twin id overrides it
    # (same lesson as openai — a scripted redeploy that wiped it broke every
    # hosted Agentforce consult).
    # ADK_A2A_ENDPOINT + A2ALAB_BRIDGE_URL: the cross-hyperscaler Strands -> ADK
    # cell (WS5). The direct route calls ADK_A2A_ENDPOINT with google-adc auth,
    # federated by the platform-scoped pair ${A2ALAB_STRANDS_GCP_AUDIENCE} /
    # ${A2ALAB_STRANDS_GCP_SA} (written by deploy/agentcore/gcp_federation.sh
    # strands, renamed to the generic A2ALAB_GCP_WORKLOAD_AUDIENCE /
    # A2ALAB_GCP_IMPERSONATE_SA in the runtime env below). The bridge route POSTs
    # to A2ALAB_BRIDGE_URL, itself federated into GCP. STRANDS_ADK_TIMEOUT_S
    # bounds both.
    ENV_KEYS=(STRANDS_MODEL_ID STRANDS_ANSWER_TIMEOUT_S STRANDS_ADK_TIMEOUT_S
              SF_MY_DOMAIN SF_AGENT_ID SF_STRANDS_AGENT_ID
              AF_SHIM_A2A_URL AF_SHIM_TIMEOUT_S
              ADK_A2A_ENDPOINT A2ALAB_BRIDGE_URL
              A2ALAB_PG_CLUSTER_ARN A2ALAB_PG_SECRET_ARN)
    # BRIDGE_TOKEN: the bridge route (ask_google_adk_bridge) authenticates to
    # the bridge with X-Bridge-Token, so the runtime needs the shared secret —
    # without it every bridge-route call 401s while the direct route works, the
    # same confusing half-failure the Dockerfile's google-auth note guards.
    SECRET_KEYS=(SF_CLIENT_ID SF_CLIENT_SECRET BRIDGE_TOKEN)
    ;;
  *) echo "unknown platform '$PLATFORM' (claude|openai|strands)"; exit 1 ;;
esac

# F1: credentials never ride the runtime config. The keys above in
# SECRET_KEYS (+ the shim bearer token) go into one Secrets Manager secret
# per runtime; the runtime carries only its ARN and interop.secret_env
# loads it at container start. Everything in ENV_KEYS is plain config —
# model names, timeouts, twin ids, endpoints — and stays visible on the
# runtime description where it is useful for debugging.
SECRET_NAME="a2alab/runtime/$PLATFORM"

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

# ---- credentials -> Secrets Manager (F1) -----------------------------------
# One secret per runtime, a JSON object of env vars — the same shape the
# harvest Lambda has used since D23. Rotating a key is a put-secret-value
# plus a container restart, not a redeploy.
SECRET_JSON=$(A2ALAB_PLATFORM="$PLATFORM" python3 - "${SECRET_KEYS[@]}" <<'PY'
import json, os, sys
env = {k: os.environ[k] for k in sys.argv[1:] if os.environ.get(k)}
# F6 per-caller Salesforce identity: if .env carries SF_CLIENT_ID_CLAUDE /
# SF_CLIENT_SECRET_CLAUDE (this runtime's own External Client App), ship
# those instead of the shared pair, so Salesforce login history attributes
# this caller by its own app. Falls back to the shared app when unset —
# which is also what keeps this script the single source of the runtime
# secret: a redeploy can't silently revert a hand-wired identity.
suffix = os.environ["A2ALAB_PLATFORM"].upper()
for key in ("SF_CLIENT_ID", "SF_CLIENT_SECRET"):
    override = os.environ.get(f"{key}_{suffix}")
    if override:
        env[key] = override
# The shim credential rides as AF_SHIM_TOKEN, never A2ALAB_TOKEN: setting
# A2ALAB_TOKEN in the runtime flips on the container's own inbound bearer
# auth, which invoke_agent_runtime cannot satisfy — every invoke 401s.
if os.environ.get("A2ALAB_TOKEN"):
    env["AF_SHIM_TOKEN"] = os.environ["A2ALAB_TOKEN"]
print(json.dumps(env))
PY
)

if SECRET_ARN=$(aws secretsmanager describe-secret --region "$REGION" \
      --secret-id "$SECRET_NAME" --query ARN --output text 2>/dev/null); then
  aws secretsmanager put-secret-value --region "$REGION" \
    --secret-id "$SECRET_NAME" --secret-string "$SECRET_JSON" >/dev/null
  echo "updated secret $SECRET_NAME"
else
  SECRET_ARN=$(aws secretsmanager create-secret --region "$REGION" --name "$SECRET_NAME" \
    --description "A2A lab: credentials for the $PLATFORM AgentCore runtime (F1)" \
    --secret-string "$SECRET_JSON" --query ARN --output text)
  echo "created secret $SECRET_NAME"
fi

# Idempotent read grant. Per-platform policy name: the claude and openai
# runtimes share one execution role, so a single name would mean each deploy
# revoked the other runtime's access.
ROLE_NAME="${ROLE_ARN##*/}"
aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "read-runtime-secret-$PLATFORM" \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"

# WS5/D66: Strands runs its model on Bedrock via the runtime's IAM role rather
# than an API key (the framework-isolation choice — same cloud/model family as
# the Claude runtime, only the SDK differs). Grant bedrock:InvokeModel on the
# strands runtime only; the claude/openai runtimes reach their models with an
# API key and get no such grant. Idempotent, per-platform policy name.
if [ "$PLATFORM" = "strands" ]; then
  aws iam put-role-policy --role-name "$ROLE_NAME" \
    --policy-name "invoke-bedrock-$PLATFORM" \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],"Resource":"*"}]}'
fi

# ---- runtime env vars (only keys that are set locally) ---------------------
# A2ALAB_TRACE_SINK=postgres: the container writes hops to the Aurora store
# (local dev uses the default jsonl sink; the runtime has no local traces/).
# The container gets the WRITER secret: local .env carries the reader (for
# console queries), but the Data API secret IS the role selection (D23) and
# a runtime inserting hops through the reader fails read-only.
ENV_JSON=$(A2ALAB_RUNTIME_SECRET_ARN="$SECRET_ARN" A2ALAB_PLATFORM="$PLATFORM" \
  python3 - "${ENV_KEYS[@]}" <<'PY'
import json, os, sys
env = {k: os.environ[k] for k in sys.argv[1:] if os.environ.get(k)}
# AWS -> GCP federation for the native-direct cross-hyperscaler leg (WS5):
# the PLATFORM-scoped pair in .env is renamed to the generic names the
# container reads (interop.cloud_auth), for the same reason deploy_fanout.sh
# does — the generic names in .env would put the LAPTOP into federation mode.
_suffix = os.environ["A2ALAB_PLATFORM"].upper()
_aud = os.environ.get(f"A2ALAB_{_suffix}_GCP_AUDIENCE")
_sa = os.environ.get(f"A2ALAB_{_suffix}_GCP_SA")
if _aud and _sa:
    env["A2ALAB_GCP_WORKLOAD_AUDIENCE"] = _aud
    env["A2ALAB_GCP_IMPERSONATE_SA"] = _sa
if env.get("A2ALAB_PG_CLUSTER_ARN"):
    env["A2ALAB_TRACE_SINK"] = "postgres"
    writer = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
    if writer:
        env["A2ALAB_PG_SECRET_ARN"] = writer
# F1: the only credential-adjacent value on the runtime config — a pointer,
# not a secret. interop.secret_env resolves it at container start.
env["A2ALAB_RUNTIME_SECRET_ARN"] = os.environ["A2ALAB_RUNTIME_SECRET_ARN"]
# WS6: the lab IdP's PUBLIC key — lets the runtime verify user JWTs
# (U3 enforcement); the signing key never leaves the laptop.
import pathlib
pub = pathlib.Path(".a2alab/lab_jwt_public.pem")
if pub.exists():
    # AgentCore env vars reject control characters — ship the PEM with
    # escaped newlines; identity.public_key() unescapes on read.
    env["A2ALAB_JWT_PUBLIC_KEY"] = pub.read_text().replace("\n", "\\n")
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
