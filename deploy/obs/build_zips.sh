#!/usr/bin/env bash
# Build the two hosted-obs Lambda zips (ADR D23) into deploy/obs/dist/.
#
#   deploy/obs/build_zips.sh
#
# a2alab-obs-mcp.zip     — the MCP server (stdlib + boto3-in-runtime only)
# a2alab-obs-harvest.zip — the harvest cron (bundles anthropic + httpx for
#                          linux/arm64 python3.12)
#
# Packaging shim: interop/__init__.py (and, for the MCP zip,
# observability/__init__.py) are replaced with empty-docstring versions so
# importing interop.trace / observability.pg doesn't drag pydantic+fastapi
# into the bundle. Only stdlib modules from those packages are shipped.
set -euo pipefail
cd "$(dirname "$0")/../.."
DIST=deploy/obs/dist
rm -rf "$DIST" && mkdir -p "$DIST/mcp" "$DIST/harvest"

# ---- MCP server zip ---------------------------------------------------------
cp -R src/obs_mcp "$DIST/mcp/obs_mcp"
# The transport lives in its own package since the fan-out server (WS7 item 4)
# became the second user of it. Omitting it here breaks the function at import,
# not at request time — a cold start that fails before any log line.
cp -R src/mcp_http "$DIST/mcp/mcp_http"
mkdir -p "$DIST/mcp/observability" "$DIST/mcp/interop"
cp src/observability/pg.py src/observability/store.py "$DIST/mcp/observability/"
cp src/interop/trace.py "$DIST/mcp/interop/"
echo '"""packaging shim (deploy/obs/build_zips.sh)"""' > "$DIST/mcp/observability/__init__.py"
echo '"""packaging shim (deploy/obs/build_zips.sh)"""' > "$DIST/mcp/interop/__init__.py"
(cd "$DIST/mcp" && zip -qr ../a2alab-obs-mcp.zip . -x '*__pycache__*')

# ---- harvest zip ------------------------------------------------------------
# google-auth: the adk source reads Cloud Logging/Monitoring through ADC.
# azure-identity + azure-monitor-query: the foundry source reads App Insights.
# Both platforms were "blocked" in the hosted harvest until 2026-07-25 purely
# because their client libraries were never in this bundle.
uv pip install --target "$DIST/harvest" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 \
  --only-binary :all: anthropic httpx google-auth azure-identity azure-monitor-query -q
cp -R src/observability "$DIST/harvest/observability"
rm -f "$DIST/harvest/observability/analyst.py"
mkdir -p "$DIST/harvest/interop"
# cloud_auth alongside trace: observability/credentials.py imports
# interop.cloud_auth for the EXPLICIT Azure service-principal credential (D39 —
# never DefaultAzureCredential). The import is lazy, inside the function, so
# omitting it does not break the bundle at load — only the foundry source, and
# only at harvest time, with `No module named 'interop.cloud_auth'` recorded as
# that platform's status while every other platform reports ok. Found 2026-07-27
# the first time this bundle was rebuilt after cloud_auth was introduced.
cp src/interop/trace.py src/interop/cloud_auth.py "$DIST/harvest/interop/"
echo '"""packaging shim (deploy/obs/build_zips.sh)"""' > "$DIST/harvest/interop/__init__.py"

# WS14: the credential-expiry collector runs on this same schedule, so the
# console's Credentials panel refreshes without anyone running a script. It is
# the SAME file the operator runs locally — shipped rather than reimplemented,
# so a hosted snapshot and a hand-run one cannot disagree about what is tracked.
# config/credentials.yaml travels with it: the declared rotations are part of
# the report and there is no API to read them from.
mkdir -p "$DIST/harvest/scripts" "$DIST/harvest/config"
cp scripts/expiry_report.py "$DIST/harvest/scripts/"
cp config/credentials.yaml "$DIST/harvest/config/" 2>/dev/null || true
# PyYAML for the declared block, boto3 is already in the Lambda runtime.
uv pip install --target "$DIST/harvest" \
  --python-platform aarch64-manylinux2014 --python-version 3.12 \
  --only-binary :all: pyyaml python-dotenv -q
(cd "$DIST/harvest" && zip -qr ../a2alab-obs-harvest.zip . -x '*__pycache__*')

ls -lh "$DIST"/*.zip
