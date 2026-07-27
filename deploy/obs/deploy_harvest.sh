#!/usr/bin/env bash
# Deploy the M11 harvest Lambda: its credentials AND its code (D23, F6).
#
#   deploy/obs/deploy_harvest.sh            # secret + code
#   deploy/obs/deploy_harvest.sh --secret   # credentials only
#   deploy/obs/deploy_harvest.sh --code     # code only
#
# Why this script exists: every other hosted seam has a deploy script that
# owns its runtime secret (deploy/agentcore/deploy.sh, deploy/shim/
# deploy_shim.sh), and each of those takes the per-caller External Client App
# from .env with a fallback to the shared app. The harvest Lambda had no such
# script — its secret was hand-written once — so when F6 split the callers
# into their own ECAs, the hosted harvest silently kept authenticating as the
# shared a2a_lab_app while the local harvest used a2a_lab_obs. Exactly the
# drift D37 warns about: config that no script owns is config nobody updates.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

MODE="${1:-all}"
REGION="${AWS_REGION:-us-east-1}"
SECRET_NAME="a2alab/obs/harvest"
FUNCTION="a2alab-obs-harvest"
ZIP="deploy/obs/dist/a2alab-obs-harvest.zip"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

# ---- credentials -----------------------------------------------------------
if [[ "$MODE" == "all" || "$MODE" == "--secret" ]]; then
  # Merge, never replace: the secret also carries ANTHROPIC_API_KEY /
  # OPENAI_API_KEY for the other platforms' log sources. Only the Salesforce
  # identity is rewritten here.
  CURRENT=$(aws secretsmanager get-secret-value --region "$REGION" \
    --secret-id "$SECRET_NAME" --query SecretString --output text)
  # The GCP service-account key is a file on disk; pass its path, not its
  # contents, so the key never lands in a shell variable or the process list.
  GCP_KEY_FILE="${A2ALAB_GCP_OBS_KEY_FILE:-}"
  SECRET_JSON=$(A2ALAB_CURRENT="$CURRENT" A2ALAB_GCP_KEY_FILE="$GCP_KEY_FILE" python3 - <<'PY'
import json, os
env = json.loads(os.environ["A2ALAB_CURRENT"])
# F6: prefer the harvest's own External Client App (a2a_lab_obs — the one
# caller that needs the `api` scope, for the Data Cloud DMO reads). Falls back
# to the shared app so an unwired .env still deploys a working harvest.
for key in ("SF_CLIENT_ID", "SF_CLIENT_SECRET"):
    value = os.environ.get(f"{key}_OBS") or os.environ.get(key)
    if value:
        env[key] = value
if os.environ.get("SF_MY_DOMAIN"):
    env["SF_MY_DOMAIN"] = os.environ["SF_MY_DOMAIN"]

# 2026-07-25: adk and foundry were absent from the hosted harvest (Aurora held
# zero rows for both while the local sqlite store had them), because the Lambda
# had neither their credentials nor their client libraries. Both are carried
# here now so the hosted coverage panel can tell "nothing to pull" apart from
# "we never asked".
for key in (
    "GOOGLE_CLOUD_PROJECT",       # adk: Cloud Logging + Monitoring
    "ADK_AGENT_ENGINE_ID",
    "AZURE_LOGS_WORKSPACE_ID",    # foundry: App Insights via Azure Monitor
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
):
    if os.environ.get(key):
        env[key] = os.environ[key]

# ADC in a Lambda: google.auth wants a FILE, the Lambda gets a secret string.
# Ship the key as JSON text; the handler writes it to /tmp at cold start.
key_file = os.environ.get("A2ALAB_GCP_KEY_FILE")
if key_file and os.path.exists(key_file):
    with open(key_file) as fh:
        env["GOOGLE_APPLICATION_CREDENTIALS_JSON"] = fh.read()
print(json.dumps(env))
PY
)
  aws secretsmanager put-secret-value --region "$REGION" \
    --secret-id "$SECRET_NAME" --secret-string "$SECRET_JSON" >/dev/null
  IDENTITY=$(A2ALAB_JSON="$SECRET_JSON" python3 -c '
import json, os
cid = json.loads(os.environ["A2ALAB_JSON"]).get("SF_CLIENT_ID", "")
print("a2a_lab_obs" if cid == os.environ.get("SF_CLIENT_ID_OBS") else "shared a2a_lab_app")')
  echo "updated secret $SECRET_NAME — Salesforce identity: $IDENTITY"
fi

# ---- code ------------------------------------------------------------------
if [[ "$MODE" == "all" || "$MODE" == "--code" ]]; then
  if [[ ! -f "$ZIP" ]]; then
    echo "building $ZIP..."
    deploy/obs/build_zips.sh >/dev/null
  fi
  aws lambda update-function-code --region "$REGION" \
    --function-name "$FUNCTION" --zip-file "fileb://$ZIP" \
    --query '[FunctionName,LastModified,CodeSize]' --output text
  aws lambda wait function-updated --region "$REGION" --function-name "$FUNCTION"
fi

# ---- prove it actually harvests -------------------------------------------
# A credential or bundle change that breaks the harvest is otherwise invisible
# until the next 6-hourly EventBridge run, in nobody's terminal.
OUT=$(mktemp)
# The harvest walks five platforms' log APIs and takes ~75s; the CLI's default
# 60s read timeout would report a failure for a run that is doing fine.
aws lambda invoke --region "$REGION" --function-name "$FUNCTION" \
  --cli-read-timeout 900 --cli-connect-timeout 60 \
  --cli-binary-format raw-in-base64-out --payload '{}' "$OUT" \
  --query '[StatusCode,FunctionError]' --output text
echo "harvest response:"
python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1])), indent=2)[:1200])' "$OUT" || cat "$OUT"
rm -f "$OUT"
