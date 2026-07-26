#!/usr/bin/env bash
# Launch the Codex CLI with CloudWatch OTLP telemetry enabled (WS9).
#
#   scripts/codex_otel.sh                 # interactive
#   scripts/codex_otel.sh exec "..."      # non-interactive
#
# Why a wrapper at all — and why Codex is NOT symmetrical with Claude Code:
#
# Claude Code takes `otelHeadersHelper`, a command it re-runs about every 29
# minutes, so the CloudWatch bearer token is fetched at runtime and never
# stored. Codex's `[otel]` block takes literal headers with `${VAR}`
# interpolation from the process environment (docs: learn.chatgpt.com →
# config-file/config-advanced) — there is no helper hook, so the token is
# resolved ONCE at launch and held for the life of the session.
#
# That is the difference worth recording rather than papering over: same
# telemetry destination, same credential, but one tool can refresh a
# short-lived token and the other cannot. This wrapper is the workaround —
# fetch with the developer's existing AWS session, export for this process
# only, never write it to a config file. A session outliving the token's
# rotation window loses telemetry; it does not fail.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

headers=$("$REPO/scripts/otel_headers.sh")
token=$(python3 -c '
import json, sys
try:
    auth = json.loads(sys.argv[1])["Authorization"]
except Exception:
    sys.exit(1)
print(auth.removeprefix("Bearer "))
' "$headers" 2>/dev/null)

if [ -z "${token:-}" ]; then
  echo "codex_otel: no CloudWatch token (is the AWS session live? aws sso login --profile lab-account)" >&2
  echo "codex_otel: starting codex WITHOUT telemetry" >&2
  exec codex "$@"
fi

export CW_METRICS_TOKEN="$token"

# Per-project attribution. Neither Claude Code nor Codex emits any built-in
# attribute naming the project, working directory or git repository — the
# Claude Code metrics reference lists session, app, user, terminal and
# per-metric attributes and nothing about where the work happened. So the
# repo identity has to be supplied, and OTEL_RESOURCE_ATTRIBUTES is the only
# hook. Derived from git rather than hardcoded so the wrapper attributes
# correctly from any checkout. No spaces are permitted in values.
project=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
# owner/name from either SSH or HTTPS remotes. Parameter expansion + awk on
# purpose: BSD sed (macOS) has no lazy quantifier, so the obvious one-line
# regex works on Linux CI and fails silently to empty here.
origin=$(git remote get-url origin 2>/dev/null || true)
repo=$(printf '%s' "${origin%.git}" | awk -F'[:/]' 'NF>=2 {print $(NF-1)"/"$NF}')
attrs="tool=codex,project=${project// /_}"
[ -n "${repo:-}" ] && attrs="$attrs,repo=${repo// /_}"
[ -n "${A2ALAB_OTEL_EXTRA_ATTRS:-}" ] && attrs="$attrs,$A2ALAB_OTEL_EXTRA_ATTRS"
export OTEL_RESOURCE_ATTRIBUTES="$attrs"

exec codex "$@"
