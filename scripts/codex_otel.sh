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
# config-file/config-reference) — there is no helper hook, so the token is
# resolved ONCE at launch and held for the life of the session.
#
# That is the difference worth recording rather than papering over: same
# telemetry destination, same credential, but one tool can refresh a
# short-lived token and the other cannot. This wrapper is the workaround —
# fetch with the developer's existing AWS session, export for this process
# only, never write it to a config file. A session outliving the token's
# rotation window loses telemetry; it does not fail.
#
# THIS PATH IS NOT YET PROVEN END TO END. Measured 2026-07-26: a real Codex
# session launched through this wrapper produced zero `codex.*` datapoints in
# CloudWatch, while the Claude Code path was landing normally against the same
# endpoint and credential. The wrapper is not the bug — `~/.codex/config.toml`
# is. Codex has THREE exporters, not one:
#
#   otel.exporter          logs/events   default: none
#   otel.trace_exporter    traces        default: none
#   otel.metrics_exporter  metrics       default: statsig  <-- NOT OTLP
#
# The config this lab shipped set `exporter` (i.e. LOGS) to the CloudWatch
# *metrics* endpoint, authenticated with a metrics-only bearer token, and never
# set `metrics_exporter` at all. Three mismatches in one line, and every one of
# them fails silently. Check config-file/config-reference for the exact
# `metrics_exporter` syntax, then re-run and confirm a datapoint at the
# destination before trusting this path — see build-notes/claude/
# 08-coding-agent-telemetry.md for the full acceptance test.
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

# The exporter is injected at launch, NOT written into ~/.codex/config.toml.
#
# Codex does not interpolate ${VAR} in otel exporter headers — that is the
# `env_http_headers` mechanism, and it belongs to MCP server config, not to
# [otel]. A config carrying `Bearer ${CW_METRICS_TOKEN}` sends that string
# verbatim and CloudWatch answers 403, with the failure visible only under
# RUST_LOG=opentelemetry=debug. So the token has to be resolved here and handed
# to codex directly. `-c` takes a dotted path whose value is parsed as TOML,
# which keeps the credential out of every config file on disk.
#
# metrics_exporter, not exporter: `exporter` is logs/events (default none) and
# `trace_exporter` is traces (default none), while metrics default to `statsig`
# — so the metrics exporter is the one that has to be named, and the other two
# are left off because this token only authenticates the metrics endpoint.
CW_ENDPOINT="${A2ALAB_CW_OTLP_METRICS_ENDPOINT:-https://monitoring.us-east-1.amazonaws.com/v1/metrics}"
otel_override="otel.metrics_exporter={ otlp-http = { endpoint = \"$CW_ENDPOINT\", protocol = \"binary\", headers = { Authorization = \"Bearer $token\" } } }"

# Per-project attribution. Neither Claude Code nor Codex emits any built-in
# attribute naming the project, working directory or git repository — the
# Claude Code metrics reference lists session, app, user, terminal and
# per-metric attributes and nothing about where the work happened. So the
# repo identity has to be supplied, and OTEL_RESOURCE_ATTRIBUTES is the only
# hook. Derived from git rather than hardcoded so the wrapper attributes
# correctly from any checkout. No spaces are permitted in values.
# owner/name from either SSH or HTTPS remotes. Parameter expansion + awk on
# purpose: BSD sed (macOS) has no lazy quantifier, so the obvious one-line
# regex works on Linux CI and fails silently to empty here.
origin=$(git remote get-url origin 2>/dev/null || true)
repo=$(printf '%s' "${origin%.git}" | awk -F'[:/]' 'NF>=2 {print $(NF-1)"/"$NF}')

# `project` is the repo's NAME, not the checkout directory's basename. Those
# differ here — the working copy is ~/projects/claude-code/rc-a2a while the
# repo is congmingwudi/a2a-lab — and Claude Code's settings say
# project=a2a-lab. Measured 2026-07-26: deriving it from the directory
# published project=rc-a2a for Codex and project=a2a-lab for Claude Code, so
# the same codebase appeared as two projects and the whole point of the label
# (comparing two tools on one dashboard) was defeated on that axis. Falls back
# to the directory only when there is no remote to read.
project="${repo##*/}"
[ -z "${project:-}" ] && project=$(basename "$(git rev-parse --show-toplevel 2>/dev/null || pwd)")
attrs="tool=codex,project=${project// /_}"
[ -n "${repo:-}" ] && attrs="$attrs,repo=${repo// /_}"
[ -n "${A2ALAB_OTEL_EXTRA_ATTRS:-}" ] && attrs="$attrs,$A2ALAB_OTEL_EXTRA_ATTRS"
export OTEL_RESOURCE_ATTRIBUTES="$attrs"

exec codex -c "$otel_override" "$@"
