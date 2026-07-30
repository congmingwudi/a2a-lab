#!/usr/bin/env bash
# Launch Claude Code with the CloudWatch OTLP **logs** signal enabled (WS16, D59).
#
#   scripts/claude_otel.sh              # interactive, logs + metrics
#   scripts/claude_otel.sh -p "..."     # non-interactive
#
# Metrics are already on for this project without any wrapper: .claude/
# settings.local.json sets CLAUDE_CODE_ENABLE_TELEMETRY=1, the metrics endpoint,
# and `otelHeadersHelper` (scripts/otel_headers.sh), which fetches the *metrics*
# bearer token at runtime and refreshes it every ~29 minutes. So a plain
# `claude` gets metrics and nothing else. This wrapper adds the second signal —
# behavioural **logs** — and only this wrapper does, on purpose (see below).
#
# Why the logs token is NOT in settings.local.json or the helper:
#
#   - It is a DIFFERENT credential. `otelHeadersHelper` returns one header set
#     for every OTLP signal, and metrics and logs are scoped to different
#     services (`cloudwatch.amazonaws.com` vs `logs.amazonaws.com`). Proven
#     2026-07-30: the metrics bearer token returns 403 against the logs
#     endpoint. Putting the logs token in the helper would break metrics, and
#     putting it in settings would break the "no bearer token on disk" posture
#     that D39/scripts/otel_headers.sh exist to hold. So the logs headers are
#     injected here, at launch, via the signal-specific
#     OTEL_EXPORTER_OTLP_LOGS_HEADERS (which the OTel spec lets override the
#     generic headers the helper sets for metrics).
#   - It is opt-IN. A plain `claude` must never attempt a logs export with the
#     wrong token — that is a guaranteed 403 on every log record. Gating logs
#     behind this wrapper keeps the default launch clean and makes "am I
#     collecting behavioural telemetry?" a visible choice, not ambient state.
#
# Content flags stay OFF. This wrapper sets no OTEL_LOG_USER_PROMPTS /
# OTEL_LOG_TOOL_* flag, so prompts, file contents and tool arguments are never
# emitted (those flags default off — they are never masked, they are never
# sent). Every WS16 insight is computed from metadata that ships regardless:
# prompt_length, tool_name, decision, duration_ms, token counts, status_code
# (D59). There is no raw content anywhere in this pipeline to leak.
#
# Two facts the AWS docs omit, both found by doing it (build note §"logs vs
# metrics", D59), and both handled below:
#   - the logs endpoint REQUIRES x-aws-log-group and x-aws-log-stream request
#     headers (absent them it 400s "headers cannot be null");
#   - the provisioner creates the log GROUP but not the STREAM — ingestion 400s
#     "log stream does not exist" until the stream is created.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Pin AWS_PROFILE from .env if the launching shell has none — same reason
# otel_headers.sh does: Claude Code's own environment is not the shell you
# configured, and the default profile is a different account that cannot see
# this secret. Silent degradation + ambient credentials is the D39 failure.
if [ -z "${AWS_PROFILE:-}" ] && [ -f "$REPO/.env" ]; then
  AWS_PROFILE=$(grep -m1 '^AWS_PROFILE=' "$REPO/.env" | cut -d= -f2-)
  export AWS_PROFILE
fi

REGION="${A2ALAB_CW_LOGS_REGION:-${AWS_REGION:-us-east-1}}"
SECRET_ID="${A2ALAB_CW_LOGS_SECRET:-a2alab/telemetry/cw-logs-api-key}"
LOG_GROUP="${A2ALAB_CW_LOGS_GROUP:-/a2alab/coding-agents/otlp}"
# One stable stream for this tool. Concurrent sessions share it; CloudWatch
# sequences OTLP ingestion server-side, so no per-session stream is needed.
LOG_STREAM="${A2ALAB_CW_LOGS_STREAM:-claude-code}"
ENDPOINT="${A2ALAB_CW_OTLP_LOGS_ENDPOINT:-https://logs.$REGION.amazonaws.com/v1/logs}"

# Fetch the logs bearer token with the developer's existing AWS session. Never
# written to disk; lives only in this process's environment for the session.
token=$(aws secretsmanager get-secret-value \
          --region "$REGION" --secret-id "$SECRET_ID" \
          --query SecretString --output text 2>/dev/null \
        | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["CW_LOGS_API_KEY"])
except Exception: pass' 2>/dev/null)

if [ -z "${token:-}" ]; then
  who=$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo '<no AWS session>')
  echo "claude_otel: no CloudWatch logs token — secret '$SECRET_ID' unreadable as $who" >&2
  echo "claude_otel: is the AWS session live? (aws sso login). Run scripts/setup_cw_logs_otlp.py --apply if the secret is missing." >&2
  echo "claude_otel: starting claude with METRICS ONLY (settings.local.json), no behavioural logs" >&2
  exec claude "$@"
fi

# The stream must exist before ingestion or the endpoint 400s. Idempotent:
# ResourceAlreadyExistsException is the success case on every launch after the
# first, so it is swallowed. Any other failure is reported but not fatal — a
# missing stream degrades to "no logs", never a broken session.
if ! aws logs create-log-stream --region "$REGION" \
       --log-group-name "$LOG_GROUP" --log-stream-name "$LOG_STREAM" 2>/dev/null; then
  : # already exists (the common case) or unauthorized — ingestion will report
fi

# Signal-specific logs config. OTEL_LOGS_EXPORTER turns the signal on;
# OTEL_EXPORTER_OTLP_LOGS_HEADERS carries the logs token AND the two AWS request
# headers the endpoint requires. These override the generic (metrics) headers
# the helper sets, so metrics keep their own token and endpoint untouched.
export OTEL_LOGS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_LOGS_PROTOCOL="${OTEL_EXPORTER_OTLP_LOGS_PROTOCOL:-http/protobuf}"
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT="$ENDPOINT"
export OTEL_EXPORTER_OTLP_LOGS_HEADERS="Authorization=Bearer $token,x-aws-log-group=$LOG_GROUP,x-aws-log-stream=$LOG_STREAM"

# Export logs promptly so a short verification session actually ships something
# before it exits (the default batch delay can outlast a one-prompt check).
export OTEL_LOGS_EXPORT_INTERVAL="${OTEL_LOGS_EXPORT_INTERVAL:-5000}"

echo "claude_otel: behavioural logs → $ENDPOINT (group $LOG_GROUP, stream $LOG_STREAM), content flags OFF" >&2
exec claude "$@"
