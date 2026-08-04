#!/usr/bin/env bash
# Kiro hook entry: emit OTLP metrics to CloudWatch for coding-agent telemetry.
#
# Called by .kiro/hooks/otel-forward.json on session, tool, file and prompt
# events. Fire-and-forget: exits 0 with empty stdout so it never pollutes the
# agent context. Metrics are a side effect.
#
# The hook receives the trigger name as $1. Unlike Cursor's cursorscope
# bridge (which has its own ingestor process), Kiro hooks are simple commands,
# so this script does the OTLP emit directly using curl against the CloudWatch
# managed OTLP metrics endpoint. The token and endpoint are read from the env
# file written by scripts/kiro_otel.sh.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ENV_FILE="${KIRO_OTEL_ENV:-$REPO/.kiro/hooks/.env}"
TRIGGER="${1:-unknown}"

# Load the env written by scripts/kiro_otel.sh — contains endpoint, token, attrs.
if [ ! -f "$ENV_FILE" ]; then
  # Not yet configured — degrade silently, never block the agent.
  exit 0
fi
# shellcheck source=/dev/null
source "$ENV_FILE"

ENDPOINT="${KIRO_OTLP_METRICS_ENDPOINT:-}"
TOKEN="${KIRO_OTLP_METRICS_TOKEN:-}"

if [ -z "$ENDPOINT" ] || [ -z "$TOKEN" ]; then
  exit 0
fi

# Map triggers to metric names. These are cumulative counters matching the
# naming convention in coding_source.py's KIRO_METRICS tuple.
case "$TRIGGER" in
  SessionStart)     METRIC="kiro_session_total";          VALUE=1 ;;
  Stop)             METRIC="kiro_session_end_total";      VALUE=1 ;;
  PostToolUse)      METRIC="kiro_tool_executions_total";  VALUE=1 ;;
  PostFileSave)     METRIC="kiro_file_saves_total";       VALUE=1 ;;
  PostFileCreate)   METRIC="kiro_file_creates_total";     VALUE=1 ;;
  PostFileDelete)   METRIC="kiro_file_deletes_total";     VALUE=1 ;;
  UserPromptSubmit) METRIC="kiro_prompt_total";           VALUE=1 ;;
  PostTaskExec)     METRIC="kiro_task_executions_total";  VALUE=1 ;;
  *)                METRIC="kiro_hook_events_total";      VALUE=1 ;;
esac

TIMESTAMP_NS=$(python3 -c 'import time; print(int(time.time_ns()))' 2>/dev/null || date +%s000000000)

# OTLP JSON metrics payload (ExportMetricsServiceRequest). One Sum datapoint
# per hook invocation — cumulative, monotonic. The CloudWatch OTLP endpoint
# accepts this directly with Bearer auth.
PAYLOAD=$(cat <<EOF
{
  "resourceMetrics": [{
    "resource": {
      "attributes": [
        {"key": "service.name", "value": {"stringValue": "kiro"}},
        {"key": "tool", "value": {"stringValue": "kiro"}},
        {"key": "project", "value": {"stringValue": "${KIRO_PROJECT:-unattributed}"}},
        {"key": "repo", "value": {"stringValue": "${KIRO_REPO:-unattributed}"}}
      ]
    },
    "scopeMetrics": [{
      "scope": {"name": "kiro-hooks", "version": "0.1.0"},
      "metrics": [{
        "name": "$METRIC",
        "sum": {
          "dataPoints": [{
            "asInt": "$VALUE",
            "timeUnixNano": "$TIMESTAMP_NS",
            "startTimeUnixNano": "$TIMESTAMP_NS",
            "attributes": [
              {"key": "trigger", "value": {"stringValue": "$TRIGGER"}}
            ]
          }],
          "aggregationTemporality": 2,
          "isMonotonic": true
        }
      }]
    }]
  }]
}
EOF
)

# Fire and forget — background the curl so the hook returns instantly.
# Timeout is aggressive (3s) because this must never delay the agent.
curl -sf --max-time 3 \
  -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "$PAYLOAD" >/dev/null 2>&1 &

# Always exit 0 with empty stdout — never block the agent, never add to context.
exit 0
