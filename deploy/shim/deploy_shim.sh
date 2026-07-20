#!/usr/bin/env bash
# Deploy the AWS-hosted Agentforce A2A shim (D28): Lambda + API Gateway.
#
#   deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh
#
# Same exposure pattern as the obs MCP endpoint (D23): the org SCP denies
# lambda:AddPermission, so no Function URL — an HTTP API invokes the
# function through an IAM integration role instead. App-layer bearer auth
# via x-lab-token (A2ALAB_TOKEN). Idempotent; writes AF_SHIM_A2A_URL back
# to .env on success.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
REGION="${AWS_REGION:-us-east-1}"
FN=a2alab-af-shim
ZIP=deploy/shim/dist/a2alab-af-shim.zip
[ -f "$ZIP" ] || { echo "run deploy/shim/build_zip.sh first"; exit 1; }

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)

# ---- execution role (basic logs only) --------------------------------------
ROLE_ARN=$(aws iam get-role --role-name a2alab-shim-lambda --query 'Role.Arn' --output text 2>/dev/null) || {
  ROLE_ARN=$(aws iam create-role --role-name a2alab-shim-lambda \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --query 'Role.Arn' --output text)
  aws iam attach-role-policy --role-name a2alab-shim-lambda \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  sleep 10  # IAM propagation before first create-function
}

ENV_VARS="Variables={SF_MY_DOMAIN=$SF_MY_DOMAIN,SF_CLIENT_ID=$SF_CLIENT_ID,SF_CLIENT_SECRET=$SF_CLIENT_SECRET,SF_AGENT_ID=$SF_AGENT_ID,SF_OPENAI_AGENT_ID=$SF_OPENAI_AGENT_ID,SF_ADK_AGENT_ID=$SF_ADK_AGENT_ID,A2ALAB_TOKEN=$A2ALAB_TOKEN,A2ALAB_TRACE_DIR=/tmp/traces,A2ALAB_TRACE_SINK=jsonl}"

if aws lambda get-function --function-name "$FN" --region "$REGION" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$ZIP" --region "$REGION" >/dev/null
  aws lambda wait function-updated --function-name "$FN" --region "$REGION"
  aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
    --environment "$ENV_VARS" --timeout 29 --memory-size 1024 >/dev/null
  echo "updated $FN"
else
  aws lambda create-function --function-name "$FN" --region "$REGION" \
    --runtime python3.12 --architectures arm64 --handler handler.handler \
    --role "$ROLE_ARN" --zip-file "fileb://$ZIP" \
    --timeout 29 --memory-size 1024 --environment "$ENV_VARS" >/dev/null
  echo "created $FN"
fi
aws lambda wait function-updated --function-name "$FN" --region "$REGION" 2>/dev/null || true
FN_ARN=$(aws lambda get-function --function-name "$FN" --region "$REGION" --query 'Configuration.FunctionArn' --output text)

# ---- API Gateway (IAM integration role — no lambda:AddPermission) ----------
API_ID=$(aws apigatewayv2 get-apis --region "$REGION" --query "Items[?Name=='$FN'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  APIGW_ROLE=$(aws iam get-role --role-name a2alab-obs-apigw --query 'Role.Arn' --output text)
  aws iam put-role-policy --role-name a2alab-obs-apigw --policy-name invoke-af-shim \
    --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"$FN_ARN\"}]}"
  API_ID=$(aws apigatewayv2 create-api --region "$REGION" --name "$FN" --protocol-type HTTP --query ApiId --output text)
  INTEG_ID=$(aws apigatewayv2 create-integration --region "$REGION" --api-id "$API_ID" \
    --integration-type AWS_PROXY --integration-uri "$FN_ARN" \
    --payload-format-version 2.0 --credentials-arn "$APIGW_ROLE" --query IntegrationId --output text)
  aws apigatewayv2 create-route --region "$REGION" --api-id "$API_ID" \
    --route-key '$default' --target "integrations/$INTEG_ID" >/dev/null
  aws apigatewayv2 create-stage --region "$REGION" --api-id "$API_ID" \
    --stage-name '$default' --auto-deploy >/dev/null
  echo "created API $API_ID"
fi
URL="https://$API_ID.execute-api.$REGION.amazonaws.com"

# The card advertises the public URL — set it now that we know it.
aws lambda update-function-configuration --function-name "$FN" --region "$REGION" \
  --environment "${ENV_VARS%\}},AF_SHIM_PUBLIC_URL=$URL/}" >/dev/null

python3 - "$URL" <<'PY'
import sys
url = sys.argv[1]
lines = open(".env").read().splitlines()
hit = False
for i, ln in enumerate(lines):
    if ln.startswith("AF_SHIM_A2A_URL="):
        lines[i] = f"AF_SHIM_A2A_URL={url}"
        hit = True
if not hit:
    lines.append(f"AF_SHIM_A2A_URL={url}")
open(".env", "w").write("\n".join(lines) + "\n")
print(f".env: AF_SHIM_A2A_URL={url}")
PY
echo "smoke: curl -s $URL/.well-known/agent-card.json | head -c 200"
