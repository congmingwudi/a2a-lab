#!/usr/bin/env bash
# OTLP auth headers for coding-agent telemetry, fetched at runtime (WS9).
#
# Claude Code calls this via `otelHeadersHelper` and uses the JSON it prints as
# the exporter's headers, refreshing roughly every 29 minutes. Codex and any
# OTel collector can read the same secret the same way.
#
# Why a helper rather than putting the token in settings or a shell profile:
# D39 says AWS auth is the only human login in the lab's path and every other
# credential is a service identity fetched WITH it. A CloudWatch bearer token
# pasted into a config file is a long-lived credential sitting on a laptop —
# exactly what that rule exists to remove. Here the token lives in Secrets
# Manager, this script fetches it with the developer's existing AWS session,
# and it is never written to disk. Rotate by resetting the service-specific
# credential and updating the secret; nothing on the laptop changes.
#
# Prints only the header JSON on success. On failure it prints an empty JSON
# object and exits 0 — a missing token must degrade to "no telemetry", never
# break the developer's session.
set -uo pipefail

SECRET_ID="${A2ALAB_CW_METRICS_SECRET:-a2alab/telemetry/cw-metrics-api-key}"
REGION="${A2ALAB_CW_METRICS_REGION:-us-east-1}"

# Pin the profile, don't inherit whatever is ambient. Claude Code runs this
# helper in its own environment, which is NOT the shell you configured — and
# with no AWS_PROFILE the CLI falls back to the default profile, a different
# account entirely. That account cannot see this secret, the helper returned
# `{}`, and because a missing token is *designed* to degrade to "no telemetry"
# rather than break the session, the exporter went quiet for days with every
# config file looking correct. Silent degradation plus ambient credential
# resolution is the same failure mode D39 was written about; the fix is the
# same one — resolve the identity explicitly.
if [ -z "${AWS_PROFILE:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/../.env" ]; then
  AWS_PROFILE=$(grep -m1 '^AWS_PROFILE=' "$(dirname "${BASH_SOURCE[0]}")/../.env" | cut -d= -f2-)
  export AWS_PROFILE
fi

raw=$(aws secretsmanager get-secret-value \
        --region "$REGION" \
        --secret-id "$SECRET_ID" \
        --query SecretString --output text 2>/dev/null) || {
  # stderr, so `--check` and a human running this by hand see WHY, while the
  # stdout contract ("valid JSON, always") stays intact for the exporter.
  echo "otel_headers: no token — secret '$SECRET_ID' unreadable as $(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '<no AWS session>')" >&2
  echo '{}'
  exit 0
}

python3 -c '
import json, sys
try:
    key = json.loads(sys.argv[1])["CW_METRICS_API_KEY"]
except Exception:
    print("{}"); sys.exit(0)
print(json.dumps({"Authorization": f"Bearer {key}"}))
' "$raw" 2>/dev/null || echo '{}'
