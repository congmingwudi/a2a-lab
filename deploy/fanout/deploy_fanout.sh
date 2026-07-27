#!/usr/bin/env bash
# Deploy the fan-out MCP server (WS7 item 4): Lambda + API Gateway.
#
#   deploy/fanout/build_zip.sh && deploy/fanout/deploy_fanout.sh
#
# Same exposure pattern as the obs MCP endpoint (D23) and the A2A shim (D28):
# the org SCP denies lambda:AddPermission, so no Function URL — an HTTP API
# invokes the function through an IAM integration role instead. App-layer
# bearer auth (A2ALAB_FANOUT_MCP_TOKEN) matches the vault static_bearer
# credential on the Anthropic side.
#
# Two constraints this script encodes, both measured rather than assumed:
#
#   API Gateway's integration timeout is 29s in this account and is NOT
#   raisable for HTTP APIs (AWS's >29s support covers Regional and private
#   REST APIs only). So the per-leg budget is 25s, leaving margin for the
#   JSON-RPC round trip, and the function timeout sits just under the gateway's
#   so the failure is OURS to describe rather than a bare 504.
#
#   The execution role name is load-bearing. GCP's principalSet member is keyed
#   on the role NAME extracted from the assumed-role ARN, so renaming the role
#   silently removes the Lambda's Google identity.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh
REGION="${AWS_REGION:-us-east-1}"
FN=a2alab-fanout-mcp
ROLE_NAME=a2alab-fanout-lambda   # must match deploy/fanout/provision_gcp_federation.py
ZIP=deploy/fanout/dist/a2alab-fanout-mcp.zip
LEG_TIMEOUT_S=25
[ -f "$ZIP" ] || { echo "run deploy/fanout/build_zip.sh first"; exit 1; }

# ---- execution role --------------------------------------------------------
ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text 2>/dev/null) || {
  ROLE_ARN=$(aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --description "A2A lab: fan-out MCP server, one tool per business unit (WS7 item 4)" \
    --query 'Role.Arn' --output text)
  aws iam attach-role-policy --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  sleep 10  # IAM propagation before the first create-function
}
echo "role: $ROLE_ARN"

# ---- credentials -> Secrets Manager (D39/F1) -------------------------------
# The Entra service principal and the MCP bearer token leave the function
# configuration entirely; handler code loads them at cold start via
# interop.secret_env. Endpoints and audiences stay plain config — they are
# addressing, not credentials.
SECRET_NAME=a2alab/runtime/fanout-mcp
SECRET_JSON=$(python3 - <<'PY'
import json, os
keys = ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
        "A2ALAB_FANOUT_MCP_TOKEN"]
print(json.dumps({k: os.environ[k] for k in keys if os.environ.get(k)}))
PY
)
if SECRET_ARN=$(aws secretsmanager describe-secret --region "$REGION" \
      --secret-id "$SECRET_NAME" --query ARN --output text 2>/dev/null); then
  aws secretsmanager put-secret-value --region "$REGION" \
    --secret-id "$SECRET_NAME" --secret-string "$SECRET_JSON" >/dev/null
  echo "updated secret $SECRET_NAME"
else
  SECRET_ARN=$(aws secretsmanager create-secret --region "$REGION" --name "$SECRET_NAME" \
    --description "A2A lab: credentials for the fan-out MCP server (WS7 item 4)" \
    --secret-string "$SECRET_JSON" --query ARN --output text)
  echo "created secret $SECRET_NAME"
fi
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name read-runtime-secret \
  --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$SECRET_ARN\"}]}"

# ---- function env ----------------------------------------------------------
# JSON rather than the CLI's Variables={...} shorthand, which cannot carry
# comma-valued vars (A2ALAB_TRACE_SINK=jsonl,postgres).
ENV_JSON=$(A2ALAB_RUNTIME_SECRET_ARN="$SECRET_ARN" A2ALAB_LEG_TIMEOUT_S="$LEG_TIMEOUT_S" python3 - <<'PY'
import json, os, pathlib, re

# Every ${VAR} config/targets.yaml expands, DERIVED rather than hand-listed.
#
# The hand-listed version shipped on the first deploy and was wrong on its
# first try: it carried FOUNDRY_PROJECT_ENDPOINT when the target expands
# AZURE_FOUNDRY_PROJECT_ENDPOINT. The endpoint became
# "/agents/.../protocols/a2a", httpx rejected a URL with no scheme, and the
# leg reported a network error — a deploy-manifest bug wearing connectivity's
# clothes, which is exactly the failure 7f0f625 spent an evening on. A list
# that has to be maintained in parallel with targets.yaml will drift again;
# reading the file cannot.
targets = pathlib.Path("config/targets.yaml").read_text()
# Comments stripped first: the file's own header explains the ${VAR} syntax
# using ${VAR} literally, and a warning about an unset var named VAR is the
# kind of noise that teaches people to ignore this warning.
body = "\n".join(ln.split("#", 1)[0] for ln in targets.splitlines())
referenced = sorted(set(re.findall(r"\$\{([A-Z0-9_]+)\}", body)))

keys = referenced + [
    "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION",
    "A2ALAB_LEG_EXPOSURE_TARGET", "A2ALAB_LEG_COMMERCIAL_TARGET", "A2ALAB_LEG_COMMS_TARGET",
    "A2ALAB_RUNTIME_SECRET_ARN", "A2ALAB_LEG_TIMEOUT_S",
]
env = {k: os.environ[k] for k in dict.fromkeys(keys) if os.environ.get(k)}

# Say what is missing rather than shipping a "" that fails as a network error.
missing = [k for k in referenced if not os.environ.get(k)]
if missing:
    import sys
    print(f"warning: targets.yaml references unset vars: {', '.join(missing)}", file=sys.stderr)

# AWS -> GCP federation. Named A2ALAB_FANOUT_GCP_* in .env and renamed to the
# generic pair here, for the same reason D40 renames its AWS vars inside the
# container: setting the generic names in .env would put the LAPTOP into
# federation mode, where there is no AWS role to present and every ADK call
# would start failing for a reason unrelated to Google.
if os.environ.get("A2ALAB_FANOUT_GCP_AUDIENCE"):
    env["A2ALAB_GCP_WORKLOAD_AUDIENCE"] = os.environ["A2ALAB_FANOUT_GCP_AUDIENCE"]
    env["A2ALAB_GCP_IMPERSONATE_SA"] = os.environ["A2ALAB_FANOUT_GCP_SA"]

env["A2ALAB_TARGETS_PATH"] = "labconfig/targets.yaml"
# Per-leg Hops must leave the function: the jsonl sink would write into a
# container that is about to disappear, which is how a hosted component ends
# up invisible in the console while working perfectly.
env["A2ALAB_TRACE_DIR"] = "/tmp/traces"
env["A2ALAB_TRACE_SINK"] = "jsonl"
cluster, writer = os.environ.get("A2ALAB_PG_CLUSTER_ARN"), os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
if cluster and writer:
    env["A2ALAB_TRACE_SINK"] = "jsonl,postgres"
    env["A2ALAB_PG_CLUSTER_ARN"] = cluster
    env["A2ALAB_PG_SECRET_ARN"] = writer
print(json.dumps({"Variables": env}))
PY
)

# Function timeout sits UNDER the gateway's 29s: if the gateway gives up first
# the client gets a bare 504 that names nothing, whereas our own timeout
# produces the '[leg unavailable: ... timed out]' marker the contract promises.
if aws lambda get-function --function-name "$FN" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$ZIP" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
    --environment "$ENV_JSON" --timeout 28 --memory-size 1024 >/dev/null
  echo "updated $FN"
else
  aws lambda create-function --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --architectures arm64 --handler fanout_mcp/lambda_entry.handler \
    --role "$ROLE_ARN" --zip-file "fileb://$ZIP" \
    --timeout 28 --memory-size 1024 --environment "$ENV_JSON" >/dev/null
  echo "created $FN"
fi
aws lambda wait function-updated --function-name "$FN" --region "$REGION" 2>/dev/null || true
FN_ARN=$(aws lambda get-function --function-name "$FN" --region "$REGION" --query 'Configuration.FunctionArn' --output text)

# The customer-comms leg is a Bedrock AgentCore runtime — SigV4, invoked with
# the function's own role. Named runtimes only, not bedrock-agentcore:*.
if [ -n "${OPENAI_AGENTCORE_ARN:-}" ]; then
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name invoke-agentcore \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"bedrock-agentcore:InvokeAgentRuntime\",\"Resource\":[\"$OPENAI_AGENTCORE_ARN\",\"$OPENAI_AGENTCORE_ARN/*\"]}]}"
fi
# Trace hops to the Aurora store over the RDS Data API.
if [ -n "${A2ALAB_PG_CLUSTER_ARN:-}" ] && [ -n "${A2ALAB_PG_WRITER_SECRET_ARN:-}" ]; then
  aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name write-trace-store \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[
      {\"Effect\":\"Allow\",\"Action\":[\"rds-data:ExecuteStatement\",\"rds-data:BatchExecuteStatement\"],\"Resource\":\"$A2ALAB_PG_CLUSTER_ARN\"},
      {\"Effect\":\"Allow\",\"Action\":\"secretsmanager:GetSecretValue\",\"Resource\":\"$A2ALAB_PG_WRITER_SECRET_ARN\"}]}"
fi

# ---- API Gateway (IAM integration role — no lambda:AddPermission) ----------
API_ID=$(aws apigatewayv2 get-apis --region "$REGION" --query "Items[?Name=='$FN'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  APIGW_ROLE=$(aws iam get-role --role-name a2alab-obs-apigw --query 'Role.Arn' --output text)
  aws iam put-role-policy --role-name a2alab-obs-apigw --policy-name invoke-fanout-mcp \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"$FN_ARN\"}]}"
  API_ID=$(aws apigatewayv2 create-api --region "$REGION" --name "$FN" --protocol-type HTTP --query ApiId --output text)
  INTEG_ID=$(aws apigatewayv2 create-integration --region "$REGION" --api-id "$API_ID" \
    --integration-type AWS_PROXY --integration-uri "$FN_ARN" \
    --payload-format-version 2.0 --credentials-arn "$APIGW_ROLE" \
    --timeout-in-millis 29000 --query IntegrationId --output text)
  aws apigatewayv2 create-route --region "$REGION" --api-id "$API_ID" \
    --route-key '$default' --target "integrations/$INTEG_ID" >/dev/null
  aws apigatewayv2 create-stage --region "$REGION" --api-id "$API_ID" \
    --stage-name '$default' --auto-deploy >/dev/null
  echo "created API $API_ID"
fi
URL="https://$API_ID.execute-api.$REGION.amazonaws.com"

python3 - "$URL" "$API_ID" <<'PY'
import json, os, pathlib, sys
p = pathlib.Path(".a2alab/fanout_mcp.json")
state = json.loads(p.read_text()) if p.exists() else {}
state["url"], state["api_id"] = sys.argv[1], sys.argv[2]
state["token"] = os.environ.get("A2ALAB_FANOUT_MCP_TOKEN", state.get("token", ""))
p.parent.mkdir(parents=True, exist_ok=True)
p.write_text(json.dumps(state, indent=1))
PY

# .a2alab/fanout_mcp.json is NOT plain endpoint config — it carries the live
# bearer token alongside the URL. Say so here: the filename reads as harmless,
# and a backup/classification pass that trusts filenames gets this wrong (D45).
echo "MCP endpoint: $URL (saved with its bearer token to .a2alab/fanout_mcp.json — secret)"
echo "smoke:  curl -s $URL/healthz"
echo "next:   uv run python scripts/setup_fanout_orchestrator.py --mcp"
