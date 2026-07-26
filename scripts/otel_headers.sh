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

raw=$(aws secretsmanager get-secret-value \
        --region "$REGION" \
        --secret-id "$SECRET_ID" \
        --query SecretString --output text 2>/dev/null) || { echo '{}'; exit 0; }

python3 -c '
import json, sys
try:
    key = json.loads(sys.argv[1])["CW_METRICS_API_KEY"]
except Exception:
    print("{}"); sys.exit(0)
print(json.dumps({"Authorization": f"Bearer {key}"}))
' "$raw" 2>/dev/null || echo '{}'
