#!/usr/bin/env bash
# Give the hosted console's Fargate task a Google identity (WS13 item 1).
#
#   deploy/console/gcp_federation.sh
#
# The console's Run buttons reach `google-adk-a2a` and the two other Agent
# Engine targets, whose auth scheme is `google-adc`. Hosted, that failed with
# `DefaultCredentialsError`: a container has no gcloud login, and unlike the
# bridge this task had never been federated. Same fix as the bridge (WS7 item
# 7) and the fan-out Lambda (D41), one role name different.
#
# It does NOT need a second workload identity pool. `a2alab-aws` already trusts
# this AWS account and keys principals on the assumed-role NAME, so binding one
# more role is the whole change — the pool is a trust relationship with an
# ACCOUNT, and the per-workload control is the principalSet binding below.
#
# Needs gcloud ADC (long-lived). Run deploy/console/deploy_console.sh first so
# the task role exists, though the binding is keyed on the role NAME and does
# not require it to exist on Google's side.
set -euo pipefail
cd "$(dirname "$0")/../.."
set -a; source .env; set +a

# No default: the project id identifies whose cloud this is and is not
# committed. It comes from .env, which is sourced above.
PROJECT="${GOOGLE_CLOUD_PROJECT:?set GOOGLE_CLOUD_PROJECT in .env}"
POOL=a2alab-aws                 # created by deploy/fanout/provision_gcp_federation.py
PROVIDER=a2alab-lambda
SA_NAME=a2alab-fanout-mcp       # same service account: same permission, same scope
TASK_ROLE=a2alab-console-task   # must match deploy/console/deploy_console.sh

NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA_EMAIL="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
MEMBER="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.aws_role/$TASK_ROLE"

echo "binding $TASK_ROLE -> $SA_EMAIL"
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="$MEMBER" >/dev/null

AUDIENCE="//iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"

# Written under CONSOLE-scoped names and renamed to the generic pair inside the
# task definition, exactly as the bridge does. Setting the generic names in
# .env would put the LAPTOP into federation mode, where there is no AWS role to
# present and every local ADK call would start failing for a reason unrelated
# to Google — the trap D40 documents for its AWS vars.
python3 - "$AUDIENCE" "$SA_EMAIL" <<'PY'
import pathlib, sys
env = pathlib.Path(".env")
lines = env.read_text().splitlines()
for var, value in (("A2ALAB_CONSOLE_GCP_AUDIENCE", sys.argv[1]),
                   ("A2ALAB_CONSOLE_GCP_SA", sys.argv[2])):
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
echo "next: deploy/console/deploy_console.sh --skip-build   # picks up the new env"
