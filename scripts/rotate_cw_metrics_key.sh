#!/usr/bin/env bash
# Rotate the CloudWatch metrics API key behind coding-agent telemetry (WS9).
#
#   scripts/rotate_cw_metrics_key.sh             # mint, store, verify, retire old
#   scripts/rotate_cw_metrics_key.sh --keep-old  # leave the previous key in place
#
# Why this exists: the key is an IAM service-specific credential for
# cloudwatch.amazonaws.com. The first one, minted 2026-07-26 with a 90-day
# expiry, died 2026-10-24. (The CLI can't set an expiry on a CloudWatch key —
# `--credential-age-days` is Bedrock-only — and keys minted here report none.) Nothing on the laptop noticed: scripts/otel_headers.sh
# still returned a header (the token was readable, just dead), CloudWatch
# answered the exporters with 403, and the harvest reported "no metrics" — the
# cost sentinel's "telemetry is stale" was the only symptom. Rotation was a
# hand-run recipe nobody wrote down; this is it, written down.
#
# The new key goes straight from IAM into Secrets Manager and is never printed.
# Nothing on the laptop changes: otel_headers.sh and codex_otel.sh fetch the
# secret at launch, so restarting claude/codex picks the new key up.
# The hosted expiry report (harvest Lambda, A2ALAB_CW_METRICS_USER) watches it.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a
source deploy/aws_preflight.sh

USER_NAME="${A2ALAB_CW_METRICS_USER:?set A2ALAB_CW_METRICS_USER in .env}"
SECRET_ID="${A2ALAB_CW_METRICS_SECRET:-a2alab/telemetry/cw-metrics-api-key}"
REGION="${A2ALAB_CW_METRICS_REGION:-us-east-1}"
ENDPOINT="https://monitoring.${REGION}.amazonaws.com/v1/metrics"
KEEP_OLD=0
[ "${1:-}" = "--keep-old" ] && KEEP_OLD=1

probe() {  # HTTP status of an empty OTLP export with the given bearer token
  curl -s -o /dev/null -w '%{http_code}' -X POST "$ENDPOINT" \
    -H "Authorization: Bearer $1" -H 'Content-Type: application/x-protobuf' --data-binary ''
}

existing=$(aws iam list-service-specific-credentials --user-name "$USER_NAME" \
  --service-name cloudwatch.amazonaws.com \
  --query 'ServiceSpecificCredentials[].[ServiceSpecificCredentialId,Status,ExpirationDate]' \
  --output text)
echo "current keys on $USER_NAME:"; echo "${existing:-  (none)}" | sed 's/^/  /'

# IAM allows two credentials per user per service. If both slots are full,
# free one by deleting an Expired/Inactive key — never an Active one.
if [ "$(echo "$existing" | grep -c .)" -ge 2 ]; then
  stale=$(echo "$existing" | awk '$2!="Active"{print $1; exit}')
  [ -n "$stale" ] || { echo "both key slots hold Active keys — delete one by hand first" >&2; exit 1; }
  echo "freeing a slot: deleting non-active key $stale"
  aws iam delete-service-specific-credential --user-name "$USER_NAME" \
    --service-specific-credential-id "$stale"
fi

old_id=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
  --query SecretString --output text 2>/dev/null \
  | python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("CW_METRICS_CREDENTIAL_ID",""))
except Exception: pass')

created=$(aws iam create-service-specific-credential --user-name "$USER_NAME" \
  --service-name cloudwatch.amazonaws.com --output json)
payload=$(python3 -c 'import json,sys
c = json.loads(sys.argv[1])["ServiceSpecificCredential"]
key = c.get("ServiceCredentialSecret") or c.get("ServicePassword")
if not key: sys.exit("no secret field in create-service-specific-credential output")
print(json.dumps({"CW_METRICS_API_KEY": key, "CW_METRICS_CREDENTIAL_ID": c["ServiceSpecificCredentialId"]}))
' "$created")
new_id=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["CW_METRICS_CREDENTIAL_ID"])' "$payload")
expires=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["ServiceSpecificCredential"].get("ExpirationDate","never"))' "$created")
unset created
echo "minted $new_id (expires $expires)"

aws secretsmanager put-secret-value --region "$REGION" --secret-id "$SECRET_ID" \
  --secret-string "$payload" --query VersionId --output text >/dev/null
unset payload
echo "stored in $SECRET_ID"

# Verify through the SAME path the exporters use, not the variable we hold.
#
# A fresh IAM credential is NOT accepted immediately: the first rotation
# (2026-11-02) probed at once, got 403, and declared failure — the same key
# returned 400 (auth passed, empty body rejected) a minute later. So retry for
# ~90s before calling it a failure. 400 on an empty export = authenticated.
token=$(bash scripts/otel_headers.sh | python3 -c 'import json,sys
print(json.load(sys.stdin).get("Authorization","").removeprefix("Bearer "))')
status=000
for wait in 0 5 10 15 20 20 20; do
  sleep "$wait"
  status=$(probe "$token")
  case "$status" in 401|403|000) echo "  probe: HTTP $status (IAM still propagating?)";; *) break;; esac
done
unset token
if [ "$status" = 401 ] || [ "$status" = 403 ] || [ "$status" = 000 ]; then
  echo "VERIFY FAILED: endpoint answered $status after ~90s — new key $new_id is stored" >&2
  echo "but unproven; old key left in place. Re-probe later before retiring anything." >&2
  exit 1
fi
echo "verified: $ENDPOINT accepts the new key (HTTP $status on an empty export)"

if [ "$KEEP_OLD" = 0 ] && [ -n "$old_id" ] && [ "$old_id" != "$new_id" ]; then
  aws iam delete-service-specific-credential --user-name "$USER_NAME" \
    --service-specific-credential-id "$old_id" 2>/dev/null \
    && echo "retired previous key $old_id" \
    || echo "previous key $old_id already gone"
fi

cat <<EOF

Done. Next:
  1. Restart claude (scripts/claude_otel.sh) and codex (scripts/codex_otel.sh) —
     the exporters read the key at launch / every ~29 min via otel_headers.sh.
  2. After ~5 minutes of use:  uv run python scripts/obs_harvest.py coding
     should report 'ok', not 'blocked'.
  3. This key expires: $expires. If it has a date, the hosted expiry report
     (deploy/obs/deploy_harvest.sh --secret) flags it at 30 days.
EOF
