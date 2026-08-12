#!/usr/bin/env bash
# Give an AgentCore runtime's execution role a Google identity so it can call
# Vertex AI Agent Engine directly (WS5 cross-hyperscaler: Strands -> Google ADK).
#
#   deploy/agentcore/gcp_federation.sh strands
#
# This is the NATIVE-DIRECT half of the Strands -> ADK cell. The container calls
# `google-adk-a2a` (auth scheme google-adc) with no Google key: its own AWS
# execution role federates into a Google service account, exactly as the fan-out
# Lambda (D41) and the hosted bridge (deploy/bridge/gcp_federation.sh) already do.
#
# It does NOT need a new workload identity pool. The `a2alab-aws` pool already
# trusts this AWS account and keys principals on the assumed-role NAME, so binding
# one more role name — the AgentCore runtime's execution role — is the whole
# change. The pool is a trust relationship with an ACCOUNT; the per-workload
# control is the principalSet binding this grants.
#
# The role NAME is read off the live runtime (get-agent-runtime -> roleArn), not
# hardcoded: the AgentCore runtimes share one execution role and it is not a value
# this repo names. Run deploy/agentcore/deploy.sh <platform> first so the runtime
# (and its role) exist.
#
# Needs gcloud ADC (long-lived) and an authenticated AWS session.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

PLATFORM="${1:?usage: gcp_federation.sh <strands>}"
REGION="${AWS_REGION:-us-east-1}"
RUNTIME_NAME="a2alab_$PLATFORM"

# No default: the project id identifies whose cloud this is and is not committed.
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
POOL=a2alab-aws                 # created by deploy/fanout/provision_gcp_federation.py
PROVIDER=a2alab-lambda
SA_NAME=a2alab-fanout-mcp       # same service account: same permission, same scope

# The runtime's execution-role NAME, read off the live runtime. The principalSet
# member is keyed on this name, so it must be the real role, not a guess.
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --region "$REGION" \
  --query "agentRuntimes[?agentRuntimeName=='$RUNTIME_NAME'].agentRuntimeId | [0]" --output text)
if [ -z "$RUNTIME_ID" ] || [ "$RUNTIME_ID" = "None" ]; then
  echo "no runtime named $RUNTIME_NAME — run deploy/agentcore/deploy.sh $PLATFORM first" >&2
  exit 1
fi
ROLE_ARN=$(aws bedrock-agentcore-control get-agent-runtime --region "$REGION" \
  --agent-runtime-id "$RUNTIME_ID" --query roleArn --output text)
TASK_ROLE="${ROLE_ARN##*/}"

NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA_EMAIL="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
MEMBER="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.aws_role/$TASK_ROLE"

echo "binding $TASK_ROLE -> $SA_EMAIL"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="$MEMBER" >/dev/null

AUDIENCE="//iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"

# Written under STRANDS-scoped names and renamed to the generic pair inside the
# runtime env (deploy/agentcore/deploy.sh). Setting the generic
# A2ALAB_GCP_WORKLOAD_AUDIENCE / A2ALAB_GCP_IMPERSONATE_SA in .env would put the
# LAPTOP into federation mode, where there is no AWS role to present and every
# local ADK call would fail for a reason unrelated to Google — the same trap D40
# documents for its AWS vars.
python3 - "$AUDIENCE" "$SA_EMAIL" "$PLATFORM" <<'PY'
import pathlib, sys
audience, sa, platform = sys.argv[1], sys.argv[2], sys.argv[3].upper()
env = pathlib.Path(".env")
lines = env.read_text().splitlines()
for var, value in ((f"A2ALAB_{platform}_GCP_AUDIENCE", audience),
                   (f"A2ALAB_{platform}_GCP_SA", sa)):
    for i, ln in enumerate(lines):
        if ln.startswith(f"{var}="):
            lines[i] = f"{var}={value}"
            break
    else:
        lines.append(f"{var}={value}")
    print(f".env: {var} set")
env.write_text("\n".join(lines) + "\n")
PY

echo
echo "audience: $AUDIENCE"
echo "next: deploy/agentcore/deploy.sh $PLATFORM   # picks up the new federation env"
