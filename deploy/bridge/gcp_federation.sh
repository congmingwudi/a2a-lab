#!/usr/bin/env bash
# Give the hosted bridge's Fargate task a Google identity (WS7 item 7).
#
#   deploy/bridge/gcp_federation.sh
#
# The bridge routes to `google-adk-a2a`, whose auth scheme is `google-adc` —
# so a bridge running in AWS needs a Google identity exactly as the fan-out
# Lambda does (D41). It does NOT need a second workload identity pool: the
# `a2alab-aws` pool already trusts this AWS account, and its attribute mapping
# keys principals on the assumed-role NAME. Binding one more role name is the
# whole change.
#
# That is worth stating because the instinct is to build a pool per workload.
# The pool is a trust relationship with an ACCOUNT; the per-workload control is
# the principalSet binding on the service account, which is what this grants.
#
# Needs gcloud ADC (long-lived). Run deploy/bridge/deploy_bridge.sh first so
# the task role exists — though the binding is keyed on the role NAME and does
# not require the role to exist yet on Google's side.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a

PROJECT="${GOOGLE_CLOUD_PROJECT:-a2a-lab-d441}"
POOL=a2alab-aws                 # created by deploy/fanout/provision_gcp_federation.py
PROVIDER=a2alab-lambda
SA_NAME=a2alab-fanout-mcp       # same service account: same permission, same scope
TASK_ROLE=a2alab-bridge-task    # must match deploy/bridge/deploy_bridge.sh

NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA_EMAIL="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
MEMBER="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.aws_role/$TASK_ROLE"

echo "binding $TASK_ROLE -> $SA_EMAIL"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="$MEMBER" >/dev/null

AUDIENCE="//iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"

# Written under BRIDGE-scoped names and renamed to the generic pair inside the
# task definition. Setting the generic names in .env would put the LAPTOP into
# federation mode, where there is no AWS role to present and every local ADK
# call would start failing for a reason unrelated to Google — the same trap
# D40 documents for its AWS vars.
python3 - "$AUDIENCE" "$SA_EMAIL" <<'PY'
import pathlib, sys
env = pathlib.Path(".env")
lines = env.read_text().splitlines()
for var, value in (("A2ALAB_BRIDGE_GCP_AUDIENCE", sys.argv[1]),
                   ("A2ALAB_BRIDGE_GCP_SA", sys.argv[2])):
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
echo "next: deploy/bridge/deploy_bridge.sh --skip-build   # picks up the new env"
