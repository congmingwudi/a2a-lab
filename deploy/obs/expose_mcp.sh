#!/usr/bin/env bash
# Expose the obs MCP Lambda publicly (ADR D23) — via API Gateway.
#
# Why not a Lambda Function URL: the runtime account's org SCP explicitly
# denies lambda:AddPermission, so a public (auth NONE) Function URL can
# never be granted invoke access — it 403s at the AWS layer ("MCP server
# initialize failed: access forbidden" on the Anthropic side). Instead an
# HTTP API invokes the function through an IAM role (integration
# credentials role a2alab-obs-apigw) — no resource-based policy needed.
# The endpoint stays bearer-token-authed at the app layer
# (A2ALAB_OBS_MCP_TOKEN); unauthenticated requests get 401.
#
#   deploy/obs/expose_mcp.sh          # code + API (profile/region from .env)
#   deploy/obs/expose_mcp.sh --code   # function code only
#   deploy/obs/expose_mcp.sh --api    # API Gateway wiring only
#
# The --code half was missing until 2026-07-27, and its absence is exactly the
# drift D37 names: nothing in the repo pushed a2alab-obs-mcp.zip, so the
# function ran hand-deployed code from whenever someone last did it by hand.
# WS12 found it the expensive way — src/obs_mcp/tools.py had been writing the
# briefs `kind` column for a day while the deployed function knew nothing
# about it. build_zips.sh built an artifact no script shipped.
#
# Idempotent: reuses the existing API if present. Writes the URL into
# .a2alab/obs_mcp.json, then run:
#   uv run python scripts/setup_obs_analyst.py --recreate --run
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

MODE="${1:-all}"
ZIP="deploy/obs/dist/a2alab-obs-mcp.zip"

# Account comes from the verified session (deploy/aws_preflight.sh), never a
# literal: a hardcoded account id is both wrong in someone else's checkout
# and an identifier this repo does not publish.
FN_ARN="arn:aws:lambda:$AWS_REGION:$AWS_ACCOUNT:function:a2alab-obs-mcp"

# ---- code ------------------------------------------------------------------
if [ "$MODE" = "all" ] || [ "$MODE" = "--code" ]; then
  if [ ! -f "$ZIP" ]; then
    echo "building $ZIP..."
    deploy/obs/build_zips.sh >/dev/null
  fi
  aws lambda update-function-code --region "$AWS_REGION" \
    --function-name a2alab-obs-mcp --zip-file "fileb://$ZIP" \
    --query '[FunctionName,LastModified,CodeSize]' --output text
  aws lambda wait function-updated --region "$AWS_REGION" --function-name a2alab-obs-mcp
fi

if [ "$MODE" = "--code" ]; then
  API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='a2alab-obs-mcp'].ApiId | [0]" --output text)
  echo "MCP endpoint unchanged: https://$API_ID.execute-api.$AWS_REGION.amazonaws.com"
  exit 0
fi

# ---- API Gateway -----------------------------------------------------------
API_ID=$(aws apigatewayv2 get-apis --query "Items[?Name=='a2alab-obs-mcp'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  ROLE_ARN=$(aws iam get-role --role-name a2alab-obs-apigw --query 'Role.Arn' --output text 2>/dev/null) || {
    ROLE_ARN=$(aws iam create-role --role-name a2alab-obs-apigw \
      --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"apigateway.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
      --query 'Role.Arn' --output text)
    aws iam put-role-policy --role-name a2alab-obs-apigw --policy-name invoke-mcp \
      --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"lambda:InvokeFunction\",\"Resource\":\"$FN_ARN\"}]}"
    sleep 8
  }
  API_ID=$(aws apigatewayv2 create-api --name a2alab-obs-mcp --protocol-type HTTP --query 'ApiId' --output text)
  INTEG=$(aws apigatewayv2 create-integration --api-id "$API_ID" \
    --integration-type AWS_PROXY --integration-uri "$FN_ARN" \
    --payload-format-version 2.0 --credentials-arn "$ROLE_ARN" \
    --query 'IntegrationId' --output text)
  aws apigatewayv2 create-route --api-id "$API_ID" --route-key '$default' --target "integrations/$INTEG" >/dev/null
  aws apigatewayv2 create-stage --api-id "$API_ID" --stage-name '$default' --auto-deploy >/dev/null
fi

URL="https://$API_ID.execute-api.$AWS_REGION.amazonaws.com"
python3 - "$URL" "$API_ID" <<'EOF'
import json, sys
from pathlib import Path
p = Path(".a2alab/obs_mcp.json")
state = json.loads(p.read_text()) if p.exists() else {}
state["url"], state["api_id"] = sys.argv[1], sys.argv[2]
p.write_text(json.dumps(state, indent=1))
EOF
echo "MCP endpoint: $URL (saved to .a2alab/obs_mcp.json)"
echo "Smoke: curl -s $URL/healthz"
